import os
import requests
from dotenv import load_dotenv

load_dotenv()

EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "false").lower() == "true"
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "MediMind <reminders@medimind.in>")
EMAIL_REPLY_TO = os.getenv("EMAIL_REPLY_TO", "")

RESEND_API_URL = "https://api.resend.com/emails"

# ────────────────────────────────────────────────────────────────────────
# IMPORTANT — Resend domain verification
# ────────────────────────────────────────────────────────────────────────
# The default "onboarding@resend.dev" sender can ONLY deliver to the
# Resend account-owner's email.  Emails to any other address are
# silently dropped or land in spam.
#
# To send to real users you MUST:
#   1. Add and verify your own domain in the Resend dashboard
#      (https://resend.com/domains)
#   2. Set EMAIL_FROM in .env to an address on that domain, e.g.
#      EMAIL_FROM=MediMind <reminders@yourdomain.com>
#
# Until a custom domain is verified, email reminders will only work
# for the account-owner's address.
# ────────────────────────────────────────────────────────────────────────


def send_email(to_email: str, subject: str, body: str, html_body: str = None) -> bool:
    """
    Send email notification via Resend HTTP API.
    
    Args:
        to_email: Recipient email address
        subject: Email subject line
        body: Plain text email body
        html_body: Optional HTML formatted email body
        
    Returns:
        bool: True if email sent successfully, False otherwise
    """
    if not EMAIL_ENABLED:
        print(f"[EMAIL] Disabled. Would send to {to_email}: {subject}")
        return False
    
    if not RESEND_API_KEY:
        print("[EMAIL] Error: RESEND_API_KEY not configured")
        return False

    # Warn if still using the shared test domain
    from_addr = EMAIL_FROM
    if "onboarding@resend.dev" in from_addr:
        print(f"[EMAIL] WARNING: Using shared 'onboarding@resend.dev' sender. "
              f"Emails will ONLY reach the Resend account-owner's inbox. "
              f"Verify a custom domain at https://resend.com/domains to send to all users.")
    
    try:
        payload = {
            "from": from_addr,
            "to": [to_email],
            "subject": subject,
            "text": body,
            "headers": {
                "X-Entity-Ref-ID": f"medimind-{to_email}-{subject}",
            },
        }

        # Add reply-to if configured
        if EMAIL_REPLY_TO:
            payload["reply_to"] = EMAIL_REPLY_TO

        if html_body:
            payload["html"] = html_body

        response = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )

        if response.status_code in (200, 201):
            data = response.json()
            print(f"[EMAIL] Sent to {to_email}: {subject} (id={data.get('id', 'N/A')})")
            return True
        elif response.status_code == 403:
            resp_body = response.text
            print(f"[EMAIL] 403 Forbidden — likely domain not verified or "
                  f"sending to non-owner with shared domain. Response: {resp_body}")
            return False
        elif response.status_code == 422:
            resp_body = response.text
            print(f"[EMAIL] 422 Validation error: {resp_body}")
            return False
        else:
            print(f"[EMAIL] Resend API error {response.status_code}: {response.text}")
            return False
        
    except requests.exceptions.Timeout:
        print(f"[EMAIL] Timeout sending to {to_email}")
        return False
    except requests.exceptions.ConnectionError:
        print(f"[EMAIL] Connection error sending to {to_email}")
        return False
    except Exception as e:
        print(f"[EMAIL] Error sending to {to_email}: {str(e)}")
        return False


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
