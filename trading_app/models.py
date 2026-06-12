from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    email = models.EmailField(unique=True)
    broker = models.CharField(max_length=100, blank=True, default='')
    two_factor_enabled = models.BooleanField(default=False)
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=12430.00)
    trial_started_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def __str__(self):
        return self.email
