import re
import socket
from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm, SetPasswordForm
from django.contrib.auth import password_validation
from django.conf import settings
from .models import User, SECURITY_QUESTIONS


DISPOSABLE_DOMAINS = {
    'mailinator.com', 'guerrillamail.com', 'temp-mail.org', 'tempmail.com',
    '10minutemail.com', 'throwawaymail.com', 'yopmail.com', 'maildrop.cc',
    'trashmail.com', 'sharklasers.com', 'getairmail.com', 'airmailhub.com',
    'emailondeck.com', 'tempinbox.com', 'fakeinbox.com', 'mailnator.com',
    'dispostable.com', 'mailexpire.com', 'spamgourmet.com', 'mytemp.email',
    'tempemail.net', 'tempinbox.co', 'instantemailaddress.com',
    'mailmetrash.com', 'mintemail.com', 'mytrashmail.com', 'trash2009.com',
    'trashymail.com', 'tyldd.com', 'uggsrock.com', 'wegwerfmail.de',
    'wh4f.org', 'whyspam.me', 'willselfdestruct.com', 'winemaven.info',
    'wronghead.com', 'wuzup.net', 'xagloo.com', 'xemaps.com',
    'xents.com', 'xmaily.com', 'xoxy.net', 'yep.it', 'yogamaven.com',
    'yopmail.fr', 'yopmail.net', 'ypmail.webarnak.fr.eu.org',
    'yuurok.com', 'zehnminutenmail.de', 'zippymail.info', 'zoaxe.com',
    'zoemail.org', 'mailmetrash.com', 'maileater.com', 'mailexpire.com',
}


def validate_email_not_disposable(email):
    domain = email.split('@')[-1].lower()
    if domain in DISPOSABLE_DOMAINS:
        raise forms.ValidationError('Disposable email addresses are not allowed.')
    if not re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', email):
        raise forms.ValidationError('Enter a valid email address.')
    common_fakes = ['test@test.com', 'a@a.com', 'user@user.com', 'email@email.com',
                    'admin@admin.com', 'mail@mail.com', 'name@name.com']
    if email.lower() in common_fakes:
        raise forms.ValidationError('Please use a real email address.')
    try:
        import dns.resolver
        if not settings.DEBUG:
            try:
                dns.resolver.resolve(domain, 'MX', lifetime=5)
            except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.Timeout,
                    dns.exception.DNSException):
                raise forms.ValidationError('Email domain does not exist or cannot receive emails.')
    except ImportError:
        pass

BROKER_CHOICES = [
    ('', 'Choose your broker'),
    ('Deriv', 'Deriv'),
    ('Binance', 'Binance'),
]


class RegisterForm(UserCreationForm):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'class': 'form-input', 'placeholder': 'you@example.com', 'id': 'reg-email'
        })
    )
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-input', 'placeholder': 'Min 8 characters', 'id': 'reg-pass'
        })
    )
    password2 = forms.CharField(
        label='Confirm Password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-input', 'placeholder': 'Repeat password', 'id': 'reg-pass2'
        })
    )
    broker = forms.ChoiceField(
        choices=BROKER_CHOICES, required=True,
        widget=forms.Select(attrs={'class': 'form-input', 'id': 'reg-broker'})
    )

    class Meta:
        model = User
        fields = ('email', 'password1', 'password2', 'broker')

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('A user with this email already exists.')
        validate_email_not_disposable(email)
        return email.lower()


class LoginForm(AuthenticationForm):
    username = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'class': 'form-input', 'placeholder': 'you@example.com', 'id': 'login-email'
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-input', 'placeholder': 'Your password', 'id': 'login-pass'
        })
    )

    def clean_username(self):
        return self.cleaned_data.get('username').lower()


class OTPForm(forms.Form):
    otp_code = forms.CharField(
        label='Verification Code',
        max_length=6, min_length=6,
        widget=forms.TextInput(attrs={
            'class': 'form-input', 'placeholder': '000000',
            'id': 'otp-code', 'autocomplete': 'off',
            'inputmode': 'numeric', 'pattern': '[0-9]*'
        })
    )


class DocumentUploadForm(forms.Form):
    title = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': 'form-input', 'placeholder': 'Document title'
        })
    )
    content = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'form-input', 'placeholder': 'Paste your document content here...',
            'rows': 10, 'style': 'resize:vertical;min-height:150px;font-family:monospace;font-size:13px'
        })
    )
    source = forms.ChoiceField(
        choices=[
            ('manual', 'Manual Entry'),
            ('web', 'Web Content'),
            ('api', 'API Import'),
        ],
        widget=forms.Select(attrs={'class': 'form-input'})
    )


class LLMQueryForm(forms.Form):
    QUERY_CHOICES = [
        ('market_analysis', 'Market Analysis'),
        ('trading_idea', 'Trading Idea'),
        ('rag_query', 'Knowledge Query (RAG)'),
    ]
    query_type = forms.ChoiceField(
        choices=QUERY_CHOICES,
        widget=forms.Select(attrs={'class': 'form-input'})
    )
    prompt = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'form-input', 'placeholder': 'Ask the AI trading assistant...',
            'rows': 4, 'style': 'resize:vertical'
        })
    )
    market_data = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-input', 'placeholder': 'Optional: paste market data for context',
            'rows': 3, 'style': 'resize:vertical;font-size:12px'
        })
    )


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'class': 'form-input', 'placeholder': 'you@example.com', 'id': 'reset-email',
            'autofocus': True,
        })
    )


class PasswordResetVerifyForm(forms.Form):
    otp_code = forms.CharField(
        label='Verification Code',
        max_length=6, min_length=6,
        widget=forms.TextInput(attrs={
            'class': 'form-input', 'placeholder': '000000',
            'id': 'reset-otp', 'autocomplete': 'off',
            'inputmode': 'numeric', 'pattern': '[0-9]*',
        })
    )


class SetNewPasswordForm(SetPasswordForm):
    new_password1 = forms.CharField(
        label='New Password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-input', 'placeholder': 'Min 8 characters', 'id': 'new-pass1',
            'autocomplete': 'new-password',
        }),
        strip=False,
        help_text=password_validation.password_validators_help_text_html(),
    )
    new_password2 = forms.CharField(
        label='Confirm New Password',
        strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-input', 'placeholder': 'Repeat new password', 'id': 'new-pass2',
            'autocomplete': 'new-password',
        }),
    )


class SecurityQuestionForm(forms.Form):
    question_key = forms.ChoiceField(
        choices=SECURITY_QUESTIONS,
        widget=forms.Select(attrs={'class': 'form-input', 'id': 'sec-q-key'})
    )
    answer = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': 'form-input', 'placeholder': 'Your answer', 'id': 'sec-q-answer',
        })
    )


class SecurityAnswerForm(forms.Form):
    answer = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': 'form-input', 'placeholder': 'Your answer', 'id': 'sec-answer',
        })
    )


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['email', 'broker', 'phone', 'bio']
        widgets = {
            'email': forms.EmailInput(attrs={
                'class': 'form-input', 'id': 'prof-email',
            }),
            'broker': forms.Select(attrs={
                'class': 'form-input', 'id': 'prof-broker',
            }, choices=BROKER_CHOICES),
            'phone': forms.TextInput(attrs={
                'class': 'form-input', 'id': 'prof-phone', 'placeholder': '+1 (555) 123-4567',
            }),
            'bio': forms.Textarea(attrs={
                'class': 'form-input', 'id': 'prof-bio', 'rows': 3,
                'placeholder': 'Tell us about yourself...',
                'style': 'resize:vertical;min-height:80px',
            }),
        }

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            existing = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError('A user with this email already exists.')
            validate_email_not_disposable(email)
        return email.lower() if email else email


class ProfilePictureForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['avatar']
        widgets = {
            'avatar': forms.FileInput(attrs={
                'class': 'form-input', 'id': 'prof-avatar', 'accept': 'image/*',
            }),
        }
