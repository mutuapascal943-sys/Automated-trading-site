def build_email_settings(debug=False, *, host, port, use_tls=False, use_ssl=False, username='', password='', from_email='', file_backend=False, base_dir=None):
    if use_ssl and use_tls:
        raise ValueError('EMAIL_USE_SSL and EMAIL_USE_TLS cannot both be True.')
    email_settings = {
        'EMAIL_HOST': host,
        'EMAIL_PORT': port,
        'EMAIL_USE_TLS': use_tls,
        'EMAIL_USE_SSL': use_ssl,
        'EMAIL_HOST_USER': username,
        'EMAIL_HOST_PASSWORD': password,
        'DEFAULT_FROM_EMAIL': from_email,
        'EMAIL_BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
    }
    if file_backend and base_dir:
        email_settings['EMAIL_BACKEND'] = 'django.core.mail.backends.filebased.EmailBackend'
        email_settings['EMAIL_FILE_PATH'] = base_dir / 'sent_emails'
    return email_settings