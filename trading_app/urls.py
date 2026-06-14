from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from . import api_views

router = DefaultRouter()
router.register(r'users', api_views.UserViewSet, basename='api_user')
router.register(r'trades', api_views.TradeViewSet, basename='api_trade')
router.register(r'signals', api_views.SignalViewSet, basename='api_signal')
router.register(r'documents', api_views.RAGDocumentViewSet, basename='api_document')

urlpatterns = [
    # Web UI
    path('', views.dashboard_view, name='dashboard'),
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('setup-2fa/', views.setup_2fa_view, name='setup_2fa'),
    path('verify-2fa/', views.verify_2fa_view, name='verify_2fa'),
    path('resend-otp/', views.resend_otp_view, name='resend_otp'),
    path('settings/profile/', views.profile_view, name='settings_profile'),
    path('settings/toggle-2fa/', views.toggle_2fa_view, name='toggle_2fa'),
    path('llm/query/', views.llm_query_view, name='llm_query'),
    path('documents/upload/', views.upload_document_view, name='upload_document'),

    # REST API
    path('api/', include(router.urls)),
    path('api/llm/query/', api_views.LLMQueryView.as_view(), name='api_llm_query'),
    path('api/market-data/', api_views.market_data_view, name='api_market_data'),
    path('api/trades/execute/', api_views.execute_trade_view, name='api_execute_trade'),
    path('api/broker/configure/', api_views.configure_broker_view, name='api_configure_broker'),
    path('api/auth/request-otp/', api_views.request_otp_view, name='api_request_otp'),
    path('api/auth/verify-otp/', api_views.verify_otp_view, name='api_verify_otp'),
    path('api/subscription/', api_views.subscription_view, name='api_subscription'),
    path('api/dashboard/stats/', api_views.dashboard_stats_view, name='api_dashboard_stats'),
]
