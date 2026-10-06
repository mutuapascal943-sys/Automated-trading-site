def build_email_settings(debug, *, host, port, use_tls, username, password, from_email, file_backend=False, base_dir=None):
    email_settings = {
        'EMAIL_HOST': host,
        'EMAIL_PORT': port,
        'EMAIL_USE_TLS': use_tls,
        'EMAIL_HOST_USER': username,
        'EMAIL_HOST_PASSWORD': password,
        'DEFAULT_FROM_EMAIL': from_email,
    }
    if not debug:
        email_settings['EMAIL_BACKEND'] = 'django.core.mail.backends.smtp.EmailBackend'
    elif file_backend:
        email_settings['EMAIL_BACKEND'] = 'django.core.mail.backends.filebased.EmailBackend'
        email_settings['EMAIL_FILE_PATH'] = base_dir / 'sent_emails'
    else:
        email_settings['EMAIL_BACKEND'] = 'django.core.mail.backends.console.EmailBackend'
    return email_settings