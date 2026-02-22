import os
from dotenv import load_dotenv

load_dotenv()

# Firebase Admin SDK initialization flag
_firebase_initialized = False

def initialize_firebase():
    """Initialize Firebase Admin SDK if not already initialized"""
    global _firebase_initialized
    
    if _firebase_initialized:
        return True
    
    try:
        import firebase_admin
        from firebase_admin import credentials
        
        # Check if already initialized
        try:
            firebase_admin.get_app()
            _firebase_initialized = True
            return True
        except ValueError:
            pass  # Not initialized yet
        
        # Try to get credentials from environment variable first (for Render/cloud deployment)
        credentials_json = os.getenv("FIREBASE_CREDENTIALS_JSON")
        
        if credentials_json:
            # Parse JSON from environment variable
            import json
            try:
                cred_dict = json.loads(credentials_json)
                cred = credentials.Certificate(cred_dict)
                print("[FCM] Using Firebase credentials from environment variable")
            except Exception as e:
                print(f"[FCM] Error parsing FIREBASE_CREDENTIALS_JSON: {e}")
                return False
        else:
            # Fallback to file path (for local development)
            cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase_credentials.json")
            
            if not os.path.exists(cred_path):
                print(f"[FCM] Warning: Firebase credentials not found")
                print("[FCM] Push notifications will be disabled. To enable:")
                print("      Option 1 (Cloud): Set FIREBASE_CREDENTIALS_JSON environment variable")
                print("      Option 2 (Local): Place firebase_credentials.json in backend root")
                print("      Get credentials from: Firebase Console -> Project Settings -> Service Accounts")
                return False
            
            cred = credentials.Certificate(cred_path)
            print(f"[FCM] Using Firebase credentials from file: {cred_path}")
        
        firebase_admin.initialize_app(cred)
        _firebase_initialized = True
        print("[FCM] Firebase Admin SDK initialized successfully")
        return True
        
    except Exception as e:
        print(f"[FCM] Error initializing Firebase: {str(e)}")
        return False


def send_push_notification(fcm_token: str, title: str, body: str, data: dict = None) -> bool:
    """
    Send a push notification to a device using Firebase Cloud Messaging
    
    Args:
        fcm_token: The device's FCM registration token
        title: Notification title
        body: Notification body text
        data: Optional data payload (for handling in app)
        
    Returns:
        bool: True if notification sent successfully, False otherwise
    """
    if not initialize_firebase():
        print("[FCM] Firebase not initialized, skipping push notification")
        return False
    
    if not fcm_token:
        print("[FCM] No FCM token provided, skipping push notification")
        return False
    
    try:
        from firebase_admin import messaging
        
        # Build the message as DATA-ONLY (no 'notification' field).
        # This prevents FCM from auto-displaying a system notification.
        # The local notifications scheduled on-device handle all visible
        # notifications — this data message is only used to update app
        # state when the app is in the foreground.
        message_data = data or {}
        # Include title/body in the data payload so the foreground handler
        # can still read them if needed.
        message_data["title"] = title
        message_data["body"] = body
        
        message = messaging.Message(
            data={k: str(v) for k, v in message_data.items()},
            token=fcm_token,
            # Android: high priority ensures delivery even in Doze mode
            android=messaging.AndroidConfig(
                priority="high",
            ),
            # iOS: set content-available for silent push
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        content_available=True,
                    )
                )
            )
        )
        
        # Send the message
        response = messaging.send(message)
        print(f"[FCM] Successfully sent message: {response}")
        return True
        
    except Exception as e:
        error_msg = str(e)
        
        # Handle specific FCM errors
        if "not-registered" in error_msg.lower() or "invalid-registration" in error_msg.lower():
            print(f"[FCM] Token is invalid or expired: {fcm_token[:20]}...")
            # TODO: Mark token as invalid in database
        elif "sender-id-mismatch" in error_msg.lower():
            print("[FCM] Sender ID mismatch - check Firebase configuration")
        else:
            print(f"[FCM] Error sending notification: {error_msg}")
        
        return False


def send_medication_reminder_push(fcm_token: str, medicine_name: str, dosage: str, timing: str) -> bool:
    """
    Send a medication reminder push notification
    
    Args:
        fcm_token: The device's FCM registration token
        medicine_name: Name of the medication
        dosage: Dosage instructions
        timing: Time of day (morning/afternoon/evening/night)
        
    Returns:
        bool: True if sent successfully
    """
    title = f"💊 Time for your {medicine_name}"
    body = f"Take {dosage} now ({timing.capitalize()})."
    
    data = {
        "type": "medication_reminder",
        "medicine_name": medicine_name,
        "dosage": dosage,
        "timing": timing,
        "screen": "dashboard"  # For app navigation
    }
    
    return send_push_notification(fcm_token, title, body, data)
