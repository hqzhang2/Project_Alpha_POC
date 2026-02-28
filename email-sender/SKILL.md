# /Users/chuck/.openclaw/workspace/email-sender/SKILL.md

# Email Sender Skill

This skill allows the agent to send emails using a Python script.
It requires `EMAIL_USER` and `EMAIL_PASSWORD` environment variables to be set on the host machine where OpenClaw is running for authentication with the SMTP server.

## Tools

### send_email
- **Description:** Sends an email to a specified recipient with a given subject and body.
- **Args:**
  - `recipient`: The email address of the recipient.
  - `subject`: The subject line of the email.
  - `body`: The content of the email.
- **Usage Example:**
  `print(default_api.exec(command='python3 /Users/chuck/.openclaw/workspace/email-sender/send_email.py "recipient@example.com" "Hello from OpenClaw" "This is the body of your email." '))`

## How to Set Up Environment Variables

**On your host machine (where OpenClaw is running), before starting OpenClaw:**

1.  **For macOS/Linux (add to `~/.zshrc`, `~/.bashrc`, or similar):**
    ```bash
    export EMAIL_USER="your_email@gmail.com"
    export EMAIL_PASSWORD="your_app_password" # Use app password for Gmail!
    ```
    Then run `source ~/.zshrc` (or your shell config file) or restart your terminal.

2.  **For Windows (PowerShell):**
    ```powershell
    $env:EMAIL_USER="your_email@gmail.com"
    $env:EMAIL_PASSWORD="your_app_password"
    ```
    For persistent variables, use `System Properties > Advanced > Environment Variables`.

**After setting the environment variables, restart OpenClaw so it picks them up.**
