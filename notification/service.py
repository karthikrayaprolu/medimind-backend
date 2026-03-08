import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
from dotenv import load_dotenv

load_dotenv()

EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "false").lower() == "true"
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "brevo").lower()  # "brevo" or "smtp"
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "MediMind <reminders@medimind.in>")
EMAIL_REPLY_TO = os.getenv("EMAIL_REPLY_TO", "")

# SMTP config (used when EMAIL_PROVIDER=smtp)
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("EMAIL_USER", "")
SMTP_PASSWORD = os.getenv("EMAIL_PASSWORD", "")

# Parse sender name and email from EMAIL_FROM (format: "Name <email>")
def _parse_sender(from_str: str):
    import re
    match = re.match(r'^(.*?)\s*<(.+?)>$', from_str.strip())
    if match:
        return {"name": match.group(1).strip(), "email": match.group(2).strip()}
    return {"name": "MediMind", "email": from_str.strip()}


def _get_brevo_api():
    """Get configured Brevo transactional email API instance"""
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key['api-key'] = BREVO_API_KEY
    return sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))


def _send_via_smtp(to_email: str, subject: str, body: str, html_body: str = None) -> bool:
    """Send email via SMTP (e.g. Gmail). Uses EMAIL_USER / EMAIL_PASSWORD from .env."""
    if not SMTP_USER or not SMTP_PASSWORD:
        print("[EMAIL] Error: SMTP credentials (EMAIL_USER / EMAIL_PASSWORD) not configured")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = to_email
        if EMAIL_REPLY_TO:
            msg["Reply-To"] = EMAIL_REPLY_TO

        msg.attach(MIMEText(body, "plain"))
        if html_body:
            msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_email, msg.as_string())

        print(f"[EMAIL] Sent to {to_email}: {subject} (via SMTP/{SMTP_SERVER})")
        return True

    except smtplib.SMTPAuthenticationError:
        print(f"[EMAIL] SMTP auth failed for {SMTP_USER}. Check EMAIL_PASSWORD (use an App Password for Gmail).")
        return False
    except Exception as e:
        print(f"[EMAIL] SMTP error sending to {to_email}: {str(e)}")
        return False


def _send_via_brevo(to_email: str, subject: str, body: str, html_body: str = None) -> bool:
    """Send email via Brevo Transactional Email API."""
    if not BREVO_API_KEY:
        print("[EMAIL] Error: BREVO_API_KEY not configured")
        return False

    try:
        api_instance = _get_brevo_api()
        sender = _parse_sender(EMAIL_FROM)

        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
            to=[{"email": to_email}],
            sender=sender,
            subject=subject,
            text_content=body,
        )

        if html_body:
            send_smtp_email.html_content = html_body

        if EMAIL_REPLY_TO:
            send_smtp_email.reply_to = {"email": EMAIL_REPLY_TO}

        api_response = api_instance.send_transac_email(send_smtp_email)
        message_id = api_response.message_id if hasattr(api_response, 'message_id') else 'N/A'
        print(f"[EMAIL] Sent to {to_email}: {subject} (id={message_id})")
        return True

    except ApiException as e:
        print(f"[EMAIL] Brevo API error sending to {to_email}: {e.status} - {e.body}")
        return False
    except Exception as e:
        print(f"[EMAIL] Error sending to {to_email}: {str(e)}")
        return False


def send_email(to_email: str, subject: str, body: str, html_body: str = None) -> bool:
    """
    Send email notification. Routes to SMTP or Brevo based on EMAIL_PROVIDER env var.

    Returns:
        bool: True if email sent successfully, False otherwise
    """
    if not EMAIL_ENABLED:
        print(f"[EMAIL] Disabled. Would send to {to_email}: {subject}")
        return False

    if EMAIL_PROVIDER == "smtp":
        return _send_via_smtp(to_email, subject, body, html_body)
    else:
        return _send_via_brevo(to_email, subject, body, html_body)


def send_medication_reminder(to_email: str, medicine_name: str, dosage: str, timing: str) -> bool:
    """
    Send medication reminder notification
    
    Args:
        to_email: User email address
        medicine_name: Name of the medication
        dosage: Dosage instructions
        timing: Time of day (morning/afternoon/evening/night)
        
    Returns:
        bool: True if sent successfully
    """
    subject = f"MediMind — {timing.capitalize()} Reminder: {medicine_name}"
    
    body = f"""
MediMind — Medication Reminder

{timing.capitalize()} Reminder

Medicine: {medicine_name}
Dosage: {dosage}
Schedule: {timing.capitalize()}

Take your medication as prescribed.

MediMind
AI-Powered Prescription Management
This is an automated reminder.
    """.strip()
    
    # Timing-specific accent bar colors and labels
    timing_config = {
        "morning": {"color": "#E8590C", "label": "Morning"},
        "afternoon": {"color": "#D97706", "label": "Afternoon"},
        "evening": {"color": "#C2410C", "label": "Evening"},
        "night": {"color": "#9A3412", "label": "Night"},
    }
    tc = timing_config.get(timing, timing_config["morning"])
    accent_color = tc["color"]
    timing_label = tc["label"]
    
    html_body = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>MediMind Reminder</title>
<!--[if mso]>
<noscript>
<xml>
<o:OfficeDocumentSettings>
<o:PixelsPerInch>96</o:PixelsPerInch>
</o:OfficeDocumentSettings>
</xml>
</noscript>
<![endif]-->
</head>
<body style="margin:0;padding:0;background-color:#f7f5f2;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;-webkit-font-smoothing:antialiased;">

<!-- Outer wrapper -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f7f5f2;">
<tr><td align="center" style="padding:40px 16px;">

<!-- Card -->
<table role="presentation" width="520" cellpadding="0" cellspacing="0" border="0" style="max-width:520px;width:100%;background-color:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.06);">

  <!-- Top accent bar -->
  <tr>
    <td style="height:4px;background-color:{accent_color};font-size:0;line-height:0;">&nbsp;</td>
  </tr>

  <!-- Logo + Brand -->
  <tr>
    <td style="padding:32px 36px 0 36px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td>
            <img src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMiIgaGVpZ2h0PSIzMiIgdmlld0JveD0iMCAwIDMyIDMyIiBmaWxsPSJub25lIj48cmVjdCB3aWR0aD0iMzIiIGhlaWdodD0iMzIiIHJ4PSI4IiBmaWxsPSIjRTg1OTBDIi8+PHJlY3QgeD0iMTMiIHk9IjciIHdpZHRoPSI2IiBoZWlnaHQ9IjE4IiByeD0iMyIgZmlsbD0id2hpdGUiLz48cmVjdCB4PSI3IiB5PSIxMyIgd2lkdGg9IjE4IiBoZWlnaHQ9IjYiIHJ4PSIzIiBmaWxsPSJ3aGl0ZSIvPjxjaXJjbGUgY3g9IjE2IiBjeT0iMTYiIHI9IjIiIGZpbGw9IiNFODU5MEMiLz48L3N2Zz4=" alt="MediMind" width="32" height="32" style="display:inline-block;vertical-align:middle;border:0;" />
            <span style="font-size:18px;font-weight:700;color:#1a1a1a;letter-spacing:-0.3px;vertical-align:middle;margin-left:10px;">MediMind</span>
          </td>
          <td style="text-align:right;">
            <span style="display:inline-block;background-color:#FFF7ED;color:{accent_color};font-size:11px;font-weight:600;padding:4px 10px;border-radius:20px;letter-spacing:0.3px;text-transform:uppercase;">{timing_label}</span>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- Divider -->
  <tr>
    <td style="padding:20px 36px 0 36px;">
      <div style="height:1px;background-color:#f0ebe6;"></div>
    </td>
  </tr>

  <!-- Heading -->
  <tr>
    <td style="padding:24px 36px 0 36px;">
      <h1 style="margin:0;font-size:22px;font-weight:700;color:#1a1a1a;letter-spacing:-0.4px;line-height:1.3;">Medication Reminder</h1>
      <p style="margin:6px 0 0 0;font-size:14px;color:#78716C;line-height:1.5;">Your scheduled {timing} dose is due.</p>
    </td>
  </tr>

  <!-- Medicine details card -->
  <tr>
    <td style="padding:20px 36px 0 36px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#FAFAF9;border:1px solid #F0EBE6;border-radius:10px;overflow:hidden;">
        
        <!-- Medicine name row -->
        <tr>
          <td style="padding:16px 20px 12px 20px;">
            <p style="margin:0;font-size:11px;font-weight:600;color:#A8A29E;text-transform:uppercase;letter-spacing:0.6px;">Medicine</p>
            <p style="margin:4px 0 0 0;font-size:17px;font-weight:700;color:{accent_color};letter-spacing:-0.2px;">{medicine_name}</p>
          </td>
        </tr>

        <!-- Separator -->
        <tr>
          <td style="padding:0 20px;">
            <div style="height:1px;background-color:#F0EBE6;"></div>
          </td>
        </tr>

        <!-- Dosage + Schedule row -->
        <tr>
          <td style="padding:12px 20px 16px 20px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td width="50%" style="vertical-align:top;">
                  <p style="margin:0;font-size:11px;font-weight:600;color:#A8A29E;text-transform:uppercase;letter-spacing:0.6px;">Dosage</p>
                  <p style="margin:4px 0 0 0;font-size:15px;font-weight:600;color:#1C1917;">{dosage}</p>
                </td>
                <td width="50%" style="vertical-align:top;">
                  <p style="margin:0;font-size:11px;font-weight:600;color:#A8A29E;text-transform:uppercase;letter-spacing:0.6px;">Schedule</p>
                  <p style="margin:4px 0 0 0;font-size:15px;font-weight:600;color:#1C1917;">{timing.capitalize()}</p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

      </table>
    </td>
  </tr>

  <!-- Reminder note -->
  <tr>
    <td style="padding:20px 36px 0 36px;">
      <p style="margin:0;font-size:13px;color:#78716C;line-height:1.6;">Take your medication as prescribed by your doctor. Consistency is key to effective treatment.</p>
    </td>
  </tr>

  <!-- Bottom spacing -->
  <tr>
    <td style="padding:32px 36px 0 36px;">
      <div style="height:1px;background-color:#f0ebe6;"></div>
    </td>
  </tr>

  <!-- Footer -->
  <tr>
    <td style="padding:20px 36px 28px 36px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td>
            <p style="margin:0;font-size:12px;font-weight:600;color:#D6D3D1;">MediMind</p>
            <p style="margin:2px 0 0 0;font-size:11px;color:#D6D3D1;line-height:1.5;">AI-Powered Prescription Management</p>
          </td>
          <td style="text-align:right;vertical-align:bottom;">
            <p style="margin:0;font-size:10px;color:#D6D3D1;">Automated reminder</p>
          </td>
        </tr>
      </table>
    </td>
  </tr>

</table>
<!-- /Card -->

</td></tr>
</table>
<!-- /Outer wrapper -->

</body>
</html>
    """.strip()
    
    return send_email(to_email, subject, body, html_body)
