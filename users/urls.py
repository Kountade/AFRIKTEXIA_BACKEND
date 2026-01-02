# urls.py
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *
from knox import views as knox_views
from django.conf import settings
from django.conf.urls.static import static
router = DefaultRouter()
router.register('register', RegisterViewset, basename='register')
router.register('login', LoginViewset, basename='login')
router.register('users', UserViewset, basename='users')
router.register('profile', ProfileViewset, basename='profile')
router.register('categories', CategorieViewSet, basename='categories')
router.register('fournisseurs', FournisseurViewSet, basename='fournisseurs')
router.register('produits', ProduitViewSet, basename='produits')
router.register('clients', ClientViewSet, basename='clients')
router.register('mouvements-stock', MouvementStockViewSet,
                basename='mouvements-stock')
router.register('entrepots', EntrepotViewSet, basename='entrepots')
router.register('stock-entrepot', StockEntrepotViewSet,
                basename='stock-entrepot')
router.register('transferts', TransfertEntrepotViewSet, basename='transferts')
router.register('ventes', VenteViewSet, basename='ventes')
router.register('dashboard', DashboardViewSet, basename='dashboard')
router.register('audit-logs', AuditLogViewSet, basename='audit-logs')
router.register('rapports', RapportsViewSet, basename='rapports')
router.register('statistiques', StatistiquesViewSet, basename='statistiques')
router.register('stock-operations', StockOperationsViewSet,
                basename='stock-operations')
router.register('stock-disponible', StockDisponibleViewSet,
                basename='stock-disponible')
router.register('historique-client', HistoriqueClientViewSet,
                basename='historique-client')
router.register('rapport-paiements', RapportPaiementsViewSet,
                basename='rapport-paiements')

urlpatterns = [
    # Vos autres URLs...
    path('', include(router.urls)),
]
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
# Ou si vous utilisez directement router.urls
# urlpatterns = router.urls
