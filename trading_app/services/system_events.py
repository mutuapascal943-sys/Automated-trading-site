from trading_app.models import SystemEvent


def record_system_event(event_type: str, market: str = '') -> None:
    if event_type not in {choice[0] for choice in SystemEvent.EVENT_CHOICES}:
        raise ValueError('Unsupported system event type.')
    safe_market = str(market)[:30] if market else ''
    SystemEvent.objects.create(event_type=event_type, market=safe_market)
    stale_ids = SystemEvent.objects.order_by('-created_at').values_list('pk', flat=True)[1000:]
    if stale_ids:
        SystemEvent.objects.filter(pk__in=stale_ids).delete()