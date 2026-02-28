
import smtplib
import os
import sys
from email.message import EmailMessage

def send_email(recipient, subject, body):
    sender_email = os.environ.get("EMAIL_USER")
    sender_password = os.environ.get("EMAIL_PASSWORD") # Or app-specific password for Gmail

    if not sender_email or not sender_password:
        print("Error: EMAIL_USER and EMAIL_PASSWORD environment variables must be set.", file=sys.stderr)
        sys.exit(1)

    # For Gmail, use 'smtp.gmail.com' and port 587
    # For other providers, check their SMTP server settings
    smtp_server = "smtp.gmail.com"
    smtp_port = 587 # For TLS

    try:
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = recipient
        msg.set_content(body)

        with smtplib.SMTP(smtp_server, smtp_port) as smtp:
            smtp.starttls() # Secure the connection
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
        print(f"Email sent successfully to {recipient}")
        sys.exit(0)
    except Exception as e:
        print(f"Error sending email: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python send_email.py <recipient> <subject> <body>", file=sys.stderr)
        sys.exit(1)
    
    recipient = sys.argv[1]
    subject = sys.argv[2]
    body = sys.argv[3]
    send_email(recipient, subject, body)
