import logging
from django.core.mail import send_mail
from django.conf import settings
from django.contrib import messages

logger = logging.getLogger(__name__)


def generate_otp(length=6):
    from trading_app.services.otp_service import generate_otp as generate_secure_otp

    return generate_secure_otp(length)


def send_otp_email(user, otp_code, request=None, purpose='2fa'):
    purpose_labels = {
        '2fa': 'Two-Factor Authentication',
        'password_reset': 'Password Recovery',
        'email_verify': 'Email Verification',
    }
    label = purpose_labels.get(purpose, 'Verification')
    subject = f'Your ATS Application {label} Code'
    message = f"""
Hello {user.email},

Your {label.lower()} code is: {otp_code}

This code will expire in {settings.OTP_EXPIRY_SECONDS // 60} minutes.

If you did not request this code, please ignore this email.

Best regards,
ATS Application Team
    """
    html_message = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; background: #0a0e1a; padding: 40px 20px;">
  <div style="max-width: 480px; margin: 0 auto; background: #131829; border-radius: 12px; padding: 32px; border: 1px solid rgba(245,194,122,0.15);">
    <div style="text-align: center; margin-bottom: 24px;">
      <span style="font-family: 'Syne',sans-serif; font-size: 22px; font-weight: 800; color: #f5c27a;">ATS Application</span>
    </div>
    <h2 style="color: #e8edf5; font-size: 18px; margin: 0 0 8px;">{label}</h2>
    <p style="color: #8a91a8; font-size: 13px; margin: 0 0 20px;">Use the code below to complete your {label.lower()}.</p>
    <div style="background: rgba(245,194,122,0.06); border-radius: 8px; padding: 20px; text-align: center; border: 1px solid rgba(245,194,122,0.1);">
      <span style="font-family: 'DM Mono', monospace; font-size: 36px; font-weight: 700; color: #f5c27a; letter-spacing: 8px;">{otp_code}</span>
    </div>
    <p style="color: #5a6178; font-size: 12px; margin: 16px 0 0; text-align: center;">
      This code expires in {settings.OTP_EXPIRY_SECONDS // 60} minutes. Never share this code with anyone.
    </p>
  </div>
</body>
</html>
    """
    try:
        sent_count = send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
        return sent_count == 1
    except Exception as exc:
        logger.warning('OTP email delivery failed (%s).', type(exc).__name__)
        if request:
            messages.error(request, 'Email delivery failed. Please try again later.')
        return False
