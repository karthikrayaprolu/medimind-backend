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


def check_and_send_reminders():
    """
    Check all enabled schedules and send reminders for current time period
    Uses USER_TIMEZONE to determine the correct timing period
    """
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(USER_TIMEZONE)
    
    print(f"[SCHEDULER] Running reminder check at {now_local.strftime('%Y-%m-%d %H:%M:%S %Z')} (UTC: {now_utc.strftime('%H:%M')})")
    
    current_hour = now_local.hour
    # Use naive UTC datetime for MongoDB comparisons (MongoDB stores naive datetimes)
    today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    
    # Determine current timing period
    if 6 <= current_hour < 12:
        timing_period = "morning"
    elif 12 <= current_hour < 17:
        timing_period = "afternoon"
    elif 17 <= current_hour < 21:
        timing_period = "evening"
    else:
        timing_period = "night"
    
    print(f"[SCHEDULER] Current timing period: {timing_period}")
    
    try:
        # Find all enabled schedules that include current timing
        # AND haven't already been sent today for this timing period
        schedules = list(sync_schedules.find({
            "enabled": True,
            "timings": timing_period
        }))
        
        print(f"[SCHEDULER] Found {len(schedules)} schedules for {timing_period}")
        
        for schedule in schedules:
            try:
                # Skip if already sent today for this timing period
                last_sent = schedule.get("last_reminder_sent")
                last_timing = schedule.get("last_reminder_timing")
                if last_sent and last_timing == timing_period:
                    if isinstance(last_sent, datetime) and last_sent >= today_start_utc:
                        print(f"[SCHEDULER] Skipping {schedule['medicine_name']}: already sent for {timing_period} today")
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
                        print(f"[SCHEDULER] Sent push notification for {schedule['medicine_name']}")
                    else:
                        print(f"[SCHEDULER] Push notification failed for {schedule['medicine_name']}")
                else:
                    print(f"[SCHEDULER] No FCM token for user {schedule['user_id']}, skipping push")
                
                if email_success or push_success:
                    # Update last_reminder_sent timestamp AND timing period to prevent duplicates
                    sync_schedules.update_one(
                        {"_id": schedule["_id"]},
                        {"$set": {
                            "last_reminder_sent": datetime.utcnow(),
                            "last_reminder_timing": timing_period
                        }}
                    )
                    print(f"[SCHEDULER] Sent reminder for {schedule['medicine_name']} to {user_email} (email={email_success}, push={push_success})")
                else:
                    print(f"[SCHEDULER] Failed to send any reminder for {schedule['medicine_name']}")
                    
            except Exception as e:
                print(f"[SCHEDULER] Error processing schedule {schedule.get('_id')}: {str(e)}")
                import traceback
                traceback.print_exc()
                continue
        
        print(f"[SCHEDULER] Reminder check completed")
        
    except Exception as e:
        print(f"[SCHEDULER] Error in check_and_send_reminders: {str(e)}")
        import traceback
        traceback.print_exc()


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
    
    # Schedule reminder checks at specific times (in IST / USER_TIMEZONE)
    # APScheduler CronTrigger uses UTC by default on servers, 
    # so we convert IST times to UTC offsets
    # IST 8:00 AM = UTC 2:30 AM
    # IST 1:00 PM = UTC 7:30 AM
    # IST 6:00 PM = UTC 12:30 PM
    # IST 9:00 PM = UTC 3:30 PM
    
    ist_offset_hours = 5
    ist_offset_minutes = 30
    
    reminder_times = [
        {"name": "Morning", "ist_hour": 8, "ist_minute": 0},
        {"name": "Afternoon", "ist_hour": 13, "ist_minute": 0},
        {"name": "Evening", "ist_hour": 18, "ist_minute": 0},
        {"name": "Night", "ist_hour": 21, "ist_minute": 0},
    ]
    
    for rt in reminder_times:
        # Convert IST to UTC
        utc_minute = rt["ist_minute"] - ist_offset_minutes
        utc_hour = rt["ist_hour"] - ist_offset_hours
        if utc_minute < 0:
            utc_minute += 60
            utc_hour -= 1
        if utc_hour < 0:
            utc_hour += 24
        
        job_id = f"{rt['name'].lower()}_reminder"
        scheduler.add_job(
            check_and_send_reminders,
            CronTrigger(hour=utc_hour, minute=utc_minute),
            id=job_id,
            name=f"{rt['name']} Medication Reminder (IST {rt['ist_hour']:02d}:{rt['ist_minute']:02d} = UTC {utc_hour:02d}:{utc_minute:02d})",
            replace_existing=True
        )
    
    scheduler.start()
    print("[SCHEDULER] Started with 4 daily reminder checks (IST 8:00, 13:00, 18:00, 21:00)")
    
    # Log next run times
    for job in scheduler.get_jobs():
        if job.id != "keepalive":
            print(f"[SCHEDULER]   {job.name} -> next run: {job.next_run_time}")


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
