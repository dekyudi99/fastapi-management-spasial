import os
import smtplib
import email.utils
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

def send_otp_email_sync(to_email: str, otp: str, purpose: str = "register"):
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    from_name = os.getenv("SMTP_FROM_NAME", "AstraGIS Platform")
    
    # Jika menggunakan Gmail SMTP, alamat pengirim WAJIB sama dengan akun SMTP yang diotentikasi
    # agar tidak dianggap spoofing / SPF failure oleh filter spam Gmail
    if "gmail.com" in smtp_host.lower():
        from_email = smtp_user
    else:
        from_email = os.getenv("SMTP_FROM_EMAIL", smtp_user)

    if purpose == "reset_password":
        subject = "Kode OTP Reset Password - AstraGIS"
        title = "Permintaan Ubah Kata Sandi"
        desc = "Kami menerima permintaan pengaturan ulang kata sandi untuk akun AstraGIS Anda. Masukkan kode OTP di bawah ini untuk melanjutkan:"
    else:
        subject = "Kode OTP Verifikasi Akun - AstraGIS"
        title = "Verifikasi Alamat Email"
        desc = "Terima kasih telah mendaftar di AstraGIS. Gunakan kode OTP di bawah ini untuk memastikan alamat email Anda valid:"

    # 1. Plain Text Alternative (Mencegah filter spam menghukum email tanpa teks murni)
    plain_text = f"""Halo,

{desc}

KODE OTP ANDA: {otp}

Kode ini hanya berlaku selama 5 menit.
Demi keamanan, JANGAN bagikan kode ini kepada siapa pun.

Jika Anda tidak melakukan permintaan ini, silakan abaikan email ini.

Salam,
Tim AstraGIS Platform
"""

    # 2. HTML Version (Layout tabel kompatibel semua client email: Gmail, Outlook, Apple Mail)
    html_content = f"""<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{subject}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
    <tr>
      <td align="center" style="padding: 30px 15px;">
        <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 500px; background-color: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); overflow: hidden;">
          <!-- Header -->
          <tr>
            <td style="background-color: #0f172a; padding: 24px; text-align: center;">
              <h1 style="margin: 0; color: #14b8a6; font-size: 24px; font-weight: 800; letter-spacing: 0.5px;">AstraGIS Platform</h1>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding: 32px 28px;">
              <h2 style="margin: 0 0 16px 0; color: #0f172a; font-size: 20px; font-weight: 700;">{title}</h2>
              <p style="margin: 0 0 24px 0; color: #475569; font-size: 15px; line-height: 1.6;">{desc}</p>
              
              <!-- OTP Box -->
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="margin: 24px 0;">
                <tr>
                  <td align="center" style="background-color: #f0fdf4; border: 2px dashed #10b981; border-radius: 10px; padding: 20px;">
                    <span style="font-family: 'Courier New', Courier, monospace; font-size: 36px; font-weight: 800; letter-spacing: 8px; color: #065f46; display: inline-block;">{otp}</span>
                  </td>
                </tr>
              </table>

              <p style="margin: 0 0 12px 0; color: #64748b; font-size: 13px; line-height: 1.5;">
                ⏰ Kode OTP ini hanya berlaku selama <strong>5 menit</strong>.
              </p>
              <p style="margin: 0; color: #94a3b8; font-size: 12px; line-height: 1.5;">
                Demi keamanan akun Anda, jangan berikan kode ini kepada pihak mana pun termasuk tim teknis.
              </p>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="background-color: #f8fafc; padding: 20px 24px; text-align: center; border-top: 1px solid #f1f5f9;">
              <p style="margin: 0; color: #94a3b8; font-size: 12px;">
                Email ini dikirim secara otomatis oleh sistem AstraGIS. Harap tidak membalas email ini.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    # Buat email multipart/alternative dengan header anti-spam RFC 5322
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain="astragis.com")
    msg["MIME-Version"] = "1.0"
    msg["X-Priority"] = "3"
    msg["Precedence"] = "bulk"
    msg["Auto-Submitted"] = "auto-generated"

    # CRITICAL: Plain text WAJIB di-attach pertama, lalu HTML kedua
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())
            print(f"[SMTP Success] Email OTP berhasil terkirim ke {to_email}")
    except Exception as e:
        print(f"[SMTP Error] Gagal mengirim email ke {to_email}: {e}")
