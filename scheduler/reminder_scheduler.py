import os
import threading
import requests
from datetime import datetime, time, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from bson import ObjectId

from db.mongo import sync_schedules, sync_users
from notification.service import send_medication_reminder
from notification.fcm import send_medication_reminder_push

scheduler = BackgroundScheduler()

# ── Timezone: Change this to your users' timezone offset ──
# IST = UTC+5:30 — adjust if your users are in a different timezone
USER_TIMEZONE = timezone(timedelta(hours=5, minutes=30))

# ── Default times for each period (used when no custom_times set) ──
DEFAULT_TIMES = {
    "morning": "08:00",
    "afternoon": "13:00",
    "evening": "18:00",
    "night": "21:00",
}

# ── Keep-alive: Prevent Render free tier from sleeping ──
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")


def keep_alive():
    """Ping our own /health endpoint to prevent Render free tier spin-down"""
    if not RENDER_EXTERNAL_URL:
        return
    try:
        url = f"{RENDER_EXTERNAL_URL}/health"
        response = requests.get(url, timeout=10)
        print(f"[KEEPALIVE] Pinged {url} -> {response.status_code}")
    except Exception as e:
        print(f"[KEEPALIVE] Ping failed: {e}")


def _get_scheduled_time(schedule: dict, timing_period: str) -> str:
    """
    Get the scheduled time string (HH:MM) for a given timing period.
    Uses custom_times if set, otherwise falls back to DEFAULT_TIMES.
    """
    custom_times = schedule.get("custom_times") or {}
    return custom_times.get(timing_period, DEFAULT_TIMES.get(timing_period, "08:00"))


def _should_send_now(schedule: dict, timing_period: str, now_local: datetime) -> bool:
    """
    Check if we should send a reminder for this schedule + timing right now.
    Returns True if the current time (HH:MM) matches the scheduled time
    within a ±2-minute window.
    """
    scheduled_time_str = _get_scheduled_time(schedule, timing_period)
    try:
        parts = scheduled_time_str.split(":")
        sched_hour = int(parts[0])
        sched_minute = int(parts[1])
    except (ValueError, IndexError):
        # Fallback to default if custom time is malformed
        default = DEFAULT_TIMES.get(timing_period, "08:00").split(":")
        sched_hour, sched_minute = int(default[0]), int(default[1])

    current_hour = now_local.hour
    current_minute = now_local.minute

    # Check within ±2 minute window to handle scheduler tick jitter
    sched_total = sched_hour * 60 + sched_minute
    current_total = current_hour * 60 + current_minute
    diff = abs(current_total - sched_total)
    return diff <= 2


def check_and_send_reminders():
    """
    Check all enabled schedules and send reminders whose custom time
    matches the current time (within ±2 min window).
    Runs every minute via the scheduler.
    """
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(USER_TIMEZONE)

    current_time_str = now_local.strftime('%H:%M')
    print(f"[SCHEDULER] Tick at {now_local.strftime('%Y-%m-%d %H:%M:%S %Z')} (UTC: {now_utc.strftime('%H:%M')})")

    # Use naive UTC datetime for MongoDB comparisons (MongoDB stores naive datetimes)
    today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

    try:
        # Fetch all enabled schedules once per tick
        all_schedules = list(sync_schedules.find({"enabled": True}))

        sent_count = 0

        for schedule in all_schedules:
            timings = schedule.get("timings", [])
            if not timings:
                continue

            for timing_period in timings:
                try:
                    # Check if this timing's custom time matches the current time
                    if not _should_send_now(schedule, timing_period, now_local):
                        continue

                    # Skip if already sent today for this timing period
                    # Use per-timing tracking dict: {"morning": "2026-02-19T...", ...}
                    sent_today_map = schedule.get("reminders_sent_today") or {}
                    last_sent_str = sent_today_map.get(timing_period)
                    if last_sent_str:
                        # Handle both datetime objects and ISO strings from DB
                        if isinstance(last_sent_str, datetime):
                            last_sent_dt = last_sent_str
                        else:
                            try:
                                last_sent_dt = datetime.fromisoformat(str(last_sent_str))
                            except (ValueError, TypeError):
                                last_sent_dt = None
                        if last_sent_dt and last_sent_dt >= today_start_utc:
                            continue

                    # Also check legacy single-field dedup for backwards compat
                    last_sent = schedule.get("last_reminder_sent")
                    last_timing = schedule.get("last_reminder_timing")
                    if last_sent and last_timing == timing_period:
                        if isinstance(last_sent, datetime) and last_sent >= today_start_utc:
                            continue

                    # Get user email and FCM token
                    user = sync_users.find_one({"_id": ObjectId(schedule["user_id"])})
                    if not user:
                        print(f"[SCHEDULER] Skipping schedule {schedule['_id']}: No user found")
                        continue

                    user_email = user.get("email")
                    if not user_email:
                        print(f"[SCHEDULER] Skipping schedule {schedule['_id']}: No user email")
                        continue

                    sched_time = _get_scheduled_time(schedule, timing_period)
                    print(f"[SCHEDULER] Time match! {schedule['medicine_name']} — {timing_period} @ {sched_time}")

                    # Send email reminder
                    email_success = send_medication_reminder(
                        to_email=user_email,
                        medicine_name=schedule["medicine_name"],
                        dosage=schedule["dosage"],
                        timing=timing_period
                    )

                    # Send push notification if user has FCM token
                    push_success = False
                    fcm_token = user.get("fcm_token")
                    if fcm_token:
                        push_success = send_medication_reminder_push(
                            fcm_token=fcm_token,
                            medicine_name=schedule["medicine_name"],
                            dosage=schedule["dosage"],
                            timing=timing_period
                        )
                        if push_success:
                            print(f"[SCHEDULER]   Push sent for {schedule['medicine_name']}")
                        else:
                            print(f"[SCHEDULER]   Push failed for {schedule['medicine_name']}")
                    else:
                        print(f"[SCHEDULER]   No FCM token for user {schedule['user_id']}, skipping push")

                    if email_success or push_success:
                        # Mark as sent so we don't duplicate today (per-timing tracking)
                        now_naive = datetime.utcnow()
                        sync_schedules.update_one(
                            {"_id": schedule["_id"]},
                            {"$set": {
                                f"reminders_sent_today.{timing_period}": now_naive,
                                "last_reminder_sent": now_naive,
                                "last_reminder_timing": timing_period
                            }}
                        )
                        sent_count += 1
                        print(f"[SCHEDULER]   ✓ Reminder sent for {schedule['medicine_name']} to {user_email} "
                              f"(email={email_success}, push={push_success})")
                    else:
                        print(f"[SCHEDULER]   ✗ Failed to send any reminder for {schedule['medicine_name']}")

                except Exception as e:
                    print(f"[SCHEDULER] Error processing schedule {schedule.get('_id')} / {timing_period}: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    continue

        if sent_count:
            print(f"[SCHEDULER] Tick done — sent {sent_count} reminder(s)")

    except Exception as e:
        print(f"[SCHEDULER] Error in check_and_send_reminders: {str(e)}")
        import traceback
        traceback.print_exc()


def _reset_daily_tracking():
    """Reset the per-timing sent tracking at midnight IST so reminders fire again."""
    try:
        result = sync_schedules.update_many(
            {"reminders_sent_today": {"$exists": True}},
            {"$set": {"reminders_sent_today": {}}}
        )
        print(f"[SCHEDULER] Daily reset: cleared reminders_sent_today for {result.modified_count} schedule(s)")
    except Exception as e:
        print(f"[SCHEDULER] Error in daily reset: {e}")


def start_scheduler():
    """Start the background scheduler for medication reminders"""
    if scheduler.running:
        print("[SCHEDULER] Already running")
        return

    # ── Keep-alive job: ping every 10 minutes to prevent Render sleep ──
    if RENDER_EXTERNAL_URL:
        scheduler.add_job(
            keep_alive,
            IntervalTrigger(minutes=10),
            id="keepalive",
            name="Keep-Alive Ping",
            replace_existing=True
        )
        print(f"[SCHEDULER] Keep-alive enabled for: {RENDER_EXTERNAL_URL}")

    # ── Run reminder check every minute so custom times are honoured ──
    scheduler.add_job(
        check_and_send_reminders,
        IntervalTrigger(minutes=1),
        id="reminder_check",
        name="Medication Reminder Check (every 1 min)",
        replace_existing=True,
    )

    # ── Daily reset at midnight IST (= 18:30 UTC previous day) ──
    scheduler.add_job(
        _reset_daily_tracking,
        CronTrigger(hour=18, minute=30),   # UTC 18:30 = IST 00:00
        id="daily_reset",
        name="Daily Reminder Tracking Reset (IST 00:00)",
        replace_existing=True,
    )

    scheduler.start()
    print("[SCHEDULER] Started — checking for due reminders every minute")
    print(f"[SCHEDULER]   Default times: {DEFAULT_TIMES}")
    print("[SCHEDULER]   Per-schedule custom_times override defaults when set")


def stop_scheduler():
    """Stop the background scheduler"""
    if scheduler.running:
        scheduler.shutdown()
        print("[SCHEDULER] Stopped")


def get_scheduler_status():
    """Get current scheduler status and jobs"""
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None
        })
    
    return {
        "running": scheduler.running,
        "jobs": jobs
    }
