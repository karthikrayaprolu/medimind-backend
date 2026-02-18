from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from auth.routes import router as auth_router
from prescription.routes import router as prescription_router
from scheduler.reminder_scheduler import start_scheduler, stop_scheduler, get_scheduler_status
from notification.fcm import initialize_firebase
import os
from dotenv import load_dotenv

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown events"""
    # Startup
    print("[APP] Starting MediMind Backend API...")
    
    # Initialize Firebase for push notifications
    if initialize_firebase():
        print("[APP] Firebase initialized for push notifications")
    else:
        print("[APP] Firebase not configured - push notifications disabled")
    
    start_scheduler()
    yield
    # Shutdown
    print("[APP] Shutting down...")
    stop_scheduler()


app = FastAPI(
    title="MediMind Backend API",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Configuration - Allow all origins for mobile app support
# Note: When allow_credentials=False, we use Authorization header instead of cookies
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for Capacitor mobile app
    allow_credentials=False,  # Must be False when using allow_origins=["*"]
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Include routers
app.include_router(auth_router, prefix="/auth", tags=["Authentication"])
app.include_router(prescription_router, prefix="/api", tags=["Prescriptions"])

@app.get("/")
async def root():
    return {
        "message": "MediMind Backend API is running",
        "version": "1.0.0",
        "endpoints": {
            "auth": "/auth",
            "prescriptions": "/api",
            "health": "/health"
        }
    }

@app.get("/health")
async def health():
    try:
        from db.mongo import sync_client
        sync_client.admin.command('ping')
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    scheduler_status = get_scheduler_status()
    
    return {
        "status": "healthy",
        "database": db_status,
        "scheduler": scheduler_status
    }


@app.post("/api/trigger-reminders")
async def trigger_reminders():
    """Manually trigger reminder check - useful for testing"""
    from scheduler.reminder_scheduler import check_and_send_reminders
    import threading
    
    # Run in background thread so we don't block the response
    thread = threading.Thread(target=check_and_send_reminders)
    thread.start()
    
    return {
        "success": True,
        "message": "Reminder check triggered. Check logs for results."
    }


@app.get("/api/debug-email")
async def debug_email():
    """Debug email configuration (no secrets exposed)"""
    from notification.service import EMAIL_ENABLED, RESEND_API_KEY, EMAIL_FROM
    
    return {
        "email_enabled": EMAIL_ENABLED,
        "transport": "Resend HTTP API",
        "resend_api_key_set": bool(RESEND_API_KEY),
        "resend_api_key_preview": RESEND_API_KEY[:8] + "***" if RESEND_API_KEY else "NOT SET",
        "email_from": EMAIL_FROM,
        "env_email_enabled_raw": os.getenv("EMAIL_ENABLED", "NOT SET"),
    }


@app.post("/api/test-email")
async def test_email():
    """Send a test email to verify Resend works on production"""
    from notification.service import send_medication_reminder, EMAIL_ENABLED, RESEND_API_KEY
    
    test_recipient = os.getenv("TEST_EMAIL_TO", "karthikrayaprolu13@gmail.com")
    
    if not EMAIL_ENABLED:
        return {
            "success": False,
            "error": "EMAIL_ENABLED is false or not set",
            "env_email_enabled": os.getenv("EMAIL_ENABLED", "NOT SET"),
        }
    
    if not RESEND_API_KEY:
        return {
            "success": False,
            "error": "RESEND_API_KEY is not set",
        }
    
    result = send_medication_reminder(
        to_email=test_recipient,
        medicine_name="Test Medicine",
        dosage="Test Dosage",
        timing="morning"
    )
    
    return {
        "success": result,
        "sent_to": test_recipient[:3] + "***",
    }
