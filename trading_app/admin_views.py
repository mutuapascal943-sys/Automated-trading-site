from django.shortcuts import render

from trading_app.admin_site import owner_admin_site
from trading_app.services.admin_access import admin_required


@admin_required
def owner_dashboard_view(request):
    context = owner_admin_site.each_context(request)
    context['title'] = 'Owner dashboard'
    return render(request, 'admin/owner_dashboard.html', context)