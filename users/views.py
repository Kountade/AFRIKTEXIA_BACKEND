from .serializers import VenteDetailSerializer, VenteCreateSerializer, VenteUpdateSerializer, EnregistrerPaiementSerializer, PaiementSerializer
from .models import Vente, StockEntrepot, Paiement, AuditLog
from django.utils import timezone
from rest_framework import status
from .serializers import TransfertEntrepotSerializer, TransfertEntrepotCreateSerializer
from .models import TransfertEntrepot, StockEntrepot
from django.shortcuts import render
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import get_user_model, authenticate
from knox.models import AuthToken
from django.db import transaction
from django.db.models import Sum, Q, Count
from datetime import datetime, timedelta
from .serializers import *
from .models import *

User = get_user_model()


class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'admin'


class IsAdminOrVendeur(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ['admin', 'vendeur']


class LoginViewset(viewsets.ViewSet):
    permission_classes = [permissions.AllowAny]
    serializer_class = LoginSerializer

    def create(self, request):
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            email = serializer.validated_data['email']
            password = serializer.validated_data['password']
            user = authenticate(request, email=email, password=password)
            if user:
                # Log de connexion
                AuditLog.objects.create(
                    user=user,
                    action='connexion',
                    modele='User',
                    objet_id=user.id,
                    details={'email': user.email}
                )

                _, token = AuthToken.objects.create(user)
                return Response({
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "role": user.role,
                        "username": user.username
                    },
                    "token": token
                })
            else:
                return Response({"error": "Invalid credentials"}, status=401)
        else:
            return Response(serializer.errors, status=400)


class RegisterViewset(viewsets.ViewSet):
    permission_classes = [permissions.AllowAny]
    queryset = User.objects.all()
    serializer_class = RegisterSerializer

    def create(self, request):
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            return Response({
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "role": user.role
                }
            }, status=status.HTTP_201_CREATED)
        else:
            return Response(serializer.errors, status=400)


class UserViewset(viewsets.ViewSet):
    permission_classes = [IsAdmin]
    queryset = User.objects.all()
    serializer_class = UserSerializer

    def list(self, request):
        if request.user.role == 'admin':
            queryset = User.objects.all()
        else:
            queryset = User.objects.filter(id=request.user.id)

        serializer = self.serializer_class(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        try:
            user = User.objects.get(pk=pk)
            if request.user.role != 'admin' and request.user.id != user.id:
                return Response({"error": "Permission denied"}, status=403)

            serializer = UserDetailSerializer(user)
            return Response(serializer.data)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)

    def update(self, request, pk=None):
        try:
            user = User.objects.get(pk=pk)
            serializer = UserDetailSerializer(
                user, data=request.data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data)
            return Response(serializer.errors, status=400)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)

    def destroy(self, request, pk=None):
        try:
            user = User.objects.get(pk=pk)

            # Empêcher la suppression de l'utilisateur super admin
            if user.is_superuser:
                return Response({"error": "Cannot delete super user"}, status=400)

            # Empêcher un utilisateur de se supprimer lui-même
            if user.id == request.user.id:
                return Response({"error": "Cannot delete yourself"}, status=400)

            user.delete()
            return Response(status=204)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)

    @action(detail=True, methods=['post'])
    def reset_password(self, request, pk=None):
        try:
            user = User.objects.get(pk=pk)
            new_password = request.data.get('new_password', 'password123')
            user.set_password(new_password)
            user.save()

            # Log d'audit
            AuditLog.objects.create(
                user=request.user,
                action='modification',
                modele='User',
                objet_id=user.id,
                details={'action': 'password_reset', 'email': user.email}
            )

            return Response({"message": "Password reset successfully"})
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)


class ProfileViewset(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserDetailSerializer

    def retrieve(self, request):
        serializer = self.serializer_class(request.user)
        return Response(serializer.data)

    def update(self, request):
        serializer = self.serializer_class(
            request.user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)


class CategorieViewSet(viewsets.ModelViewSet):
    serializer_class = CategorieSerializer
    permission_classes = [IsAdmin]

    def get_queryset(self):
        return Categorie.objects.all()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class FournisseurViewSet(viewsets.ModelViewSet):
    serializer_class = FournisseurSerializer
    permission_classes = [IsAdmin]

    def get_queryset(self):
        return Fournisseur.objects.all()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class ProduitViewSet(viewsets.ModelViewSet):
    serializer_class = ProduitSerializer
    permission_classes = [IsAdminOrVendeur]

    def get_serializer_context(self):
        """Ajoute le contexte de la requête au serializer"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    def get_queryset(self):
        # LES VENDEURS VOIENT TOUS LES PRODUITS POUR POUVOIR VENDRE
        queryset = Produit.objects.all()

        # Filtre par catégorie
        categorie_id = self.request.query_params.get('categorie')
        if categorie_id:
            queryset = queryset.filter(categorie_id=categorie_id)

        # Filtre stock faible
        low_stock = self.request.query_params.get('low_stock')
        if low_stock:
            produits_ids = [p.id for p in queryset if p.stock_faible()]
            queryset = queryset.filter(id__in=produits_ids)

        # Filtre rupture de stock
        out_of_stock = self.request.query_params.get('out_of_stock')
        if out_of_stock:
            produits_ids = [p.id for p in queryset if p.en_rupture()]
            queryset = queryset.filter(id__in=produits_ids)

        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class ClientViewSet(viewsets.ModelViewSet):
    serializer_class = ClientSerializer
    permission_classes = [IsAdminOrVendeur]

    def get_queryset(self):
        # LES VENDEURS VOIENT TOUS LES CLIENTS POUR POUVOIR VENDRE
        return Client.objects.all()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class MouvementStockViewSet(viewsets.ModelViewSet):
    serializer_class = MouvementStockSerializer
    permission_classes = [IsAdmin]

    def get_queryset(self):
        queryset = MouvementStock.objects.all().order_by('-created_at')

        # Filtre par entrepôt
        entrepot_id = self.request.query_params.get('entrepot')
        if entrepot_id:
            queryset = queryset.filter(entrepot_id=entrepot_id)

        # Filtre par produit
        produit_id = self.request.query_params.get('produit')
        if produit_id:
            queryset = queryset.filter(produit_id=produit_id)

        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


# Nouvelles vues pour les entrepôts
class EntrepotViewSet(viewsets.ModelViewSet):
    serializer_class = EntrepotSerializer
    permission_classes = [IsAdminOrVendeur]  # MODIFIÉ: Changé à IsAdminOrVendeur

    def get_queryset(self):
        # LES VENDEURS VOIENT TOUS LES ENTREPÔTS ACTIFS
        return Entrepot.objects.filter(actif=True)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class StockEntrepotViewSet(viewsets.ModelViewSet):
    serializer_class = StockEntrepotSerializer
    permission_classes = [IsAdminOrVendeur]

    def get_queryset(self):
        queryset = StockEntrepot.objects.all()

        # Filtre par entrepôt
        entrepot_id = self.request.query_params.get('entrepot')
        if entrepot_id:
            queryset = queryset.filter(entrepot_id=entrepot_id)

        # Filtre par produit
        produit_id = self.request.query_params.get('produit')
        if produit_id:
            queryset = queryset.filter(produit_id=produit_id)

        # Filtre stock faible
        low_stock = self.request.query_params.get('low_stock')
        if low_stock:
            stocks_ids = [s.id for s in queryset if s.stock_faible]
            queryset = queryset.filter(id__in=stocks_ids)

        # Filtre rupture de stock
        out_of_stock = self.request.query_params.get('out_of_stock')
        if out_of_stock:
            stocks_ids = [s.id for s in queryset if s.en_rupture]
            queryset = queryset.filter(id__in=stocks_ids)

        return queryset

    @action(detail=False, methods=['get'])
    def stock_global(self, request):
        """Retourne le stock global de tous les produits par entrepôt"""
        entrepot_id = request.query_params.get('entrepot')

        if entrepot_id:
            stocks = StockEntrepot.objects.filter(entrepot_id=entrepot_id)
        else:
            stocks = StockEntrepot.objects.all()

        # Agrégation par produit
        data = []
        produits_ids = stocks.values_list('produit_id', flat=True).distinct()

        for produit_id in produits_ids:
            produit_stocks = stocks.filter(produit_id=produit_id)
            produit = Produit.objects.get(id=produit_id)

            total_quantite = produit_stocks.aggregate(
                Sum('quantite'))['quantite__sum'] or 0
            total_reservee = produit_stocks.aggregate(Sum('quantite_reservee'))[
                'quantite_reservee__sum'] or 0

            data.append({
                'produit_id': produit_id,
                'produit_nom': produit.nom,
                'produit_code': produit.code,
                'total_quantite': total_quantite,
                'total_reservee': total_reservee,
                'total_disponible': total_quantite - total_reservee,
                'stocks_par_entrepot': StockEntrepotSerializer(
                    produit_stocks, many=True
                ).data
            })

        return Response(data)


class StockDisponibleViewSet(viewsets.ViewSet):
    """API pour récupérer les stocks disponibles par produit"""
    permission_classes = [IsAdminOrVendeur]

    def list(self, request):
        produit_id = request.query_params.get('produit')

        if not produit_id:
            return Response({'error': 'Paramètre produit requis'}, status=400)

        try:
            produit = Produit.objects.get(id=produit_id)
        except Produit.DoesNotExist:
            return Response({'error': 'Produit non trouvé'}, status=404)

        # Récupérer les stocks par entrepôt
        stocks = StockEntrepot.objects.filter(produit=produit)

        data = []
        for stock in stocks:
            data.append({
                'entrepot_id': stock.entrepot.id,
                'entrepot_nom': stock.entrepot.nom,
                'quantite_disponible': stock.quantite_disponible,
                'quantite_totale': stock.quantite,
                'quantite_reservee': stock.quantite_reservee,
                'stock_alerte': stock.stock_alerte,
                'en_rupture': stock.en_rupture,
                'stock_faible': stock.stock_faible
            })

        return Response({
            'produit': {
                'id': produit.id,
                'nom': produit.nom,
                'code': produit.code
            },
            'stocks': data
        })


# views.py - TransfertEntrepotViewSet
class TransfertEntrepotViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdmin]  # Seuls les admins peuvent faire des transferts

    def get_serializer_class(self):
        if self.action == 'create':
            return TransfertEntrepotCreateSerializer
        return TransfertEntrepotSerializer

    def get_queryset(self):
        queryset = TransfertEntrepot.objects.all().order_by('-created_at')

        # Filtrage par statut
        statut = self.request.query_params.get('statut')
        if statut:
            queryset = queryset.filter(statut=statut)

        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def confirmer(self, request, pk=None):
        """Confirmer un transfert"""
        try:
            transfert = self.get_object()

            if transfert.statut != 'brouillon':
                return Response(
                    {"detail": "Seuls les transferts en brouillon peuvent être confirmés."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Vérifier les stocks
            for ligne in transfert.lignes_transfert.all():
                try:
                    stock_source = StockEntrepot.objects.get(
                        produit=ligne.produit,
                        entrepot=transfert.entrepot_source
                    )

                    if ligne.quantite > stock_source.quantite_disponible:
                        return Response(
                            {"detail": f"Stock insuffisant pour {ligne.produit.nom}."},
                            status=status.HTTP_400_BAD_REQUEST
                        )

                except StockEntrepot.DoesNotExist:
                    return Response(
                        {"detail": f"Produit {ligne.produit.nom} non disponible dans {transfert.entrepot_source.nom}."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            # Confirmer le transfert
            transfert.confirmer_transfert()

            return Response(
                {"detail": "Transfert confirmé avec succès.",
                    "transfert": TransfertEntrepotSerializer(transfert).data},
                status=status.HTTP_200_OK
            )

        except TransfertEntrepot.DoesNotExist:
            return Response(
                {"detail": "Transfert non trouvé."},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['post'])
    def annuler(self, request, pk=None):
        """Annuler un transfert"""
        try:
            transfert = self.get_object()

            if transfert.statut != 'brouillon':
                return Response(
                    {"detail": "Seuls les transferts en brouillon peuvent être annulés."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            transfert.statut = 'annule'
            transfert.save()

            return Response(
                {"detail": "Transfert annulé avec succès."},
                status=status.HTTP_200_OK
            )

        except TransfertEntrepot.DoesNotExist:
            return Response(
                {"detail": "Transfert non trouvé."},
                status=status.HTTP_404_NOT_FOUND
            )


class VenteViewSet(viewsets.ModelViewSet):
    serializer_class = VenteDetailSerializer
    permission_classes = [IsAdminOrVendeur]

    def get_queryset(self):
        user = self.request.user
        queryset = Vente.objects.all().order_by('-created_at')

        # Filtres supplémentaires
        statut_paiement = self.request.query_params.get('statut_paiement')
        if statut_paiement:
            queryset = queryset.filter(statut_paiement=statut_paiement)

        client_id = self.request.query_params.get('client')
        if client_id:
            queryset = queryset.filter(client_id=client_id)

        en_retard = self.request.query_params.get('en_retard')
        if en_retard:
            queryset = queryset.filter(
                date_echeance__lt=timezone.now().date(),
                statut_paiement__in=['non_paye', 'partiel']
            )

        # Les vendeurs ne voient que leurs propres ventes
        if user.role != 'admin':
            queryset = queryset.filter(created_by=user)

        return queryset

    def get_serializer_class(self):
        if self.action == 'create':
            return VenteCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return VenteUpdateSerializer
        elif self.action == 'enregistrer_paiement':
            return EnregistrerPaiementSerializer
        return VenteDetailSerializer

    @action(detail=True, methods=['post'])
    def confirmer(self, request, pk=None):
        """Confirmer une vente"""
        try:
            vente = self.get_object()

            if vente.statut != 'brouillon':
                return Response(
                    {"error": "Seules les ventes en brouillon peuvent être confirmées"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Confirmer la vente
            vente.confirmer_vente()

            # Rafraîchir les données
            vente.refresh_from_db()

            return Response({
                "message": "Vente confirmée avec succès",
                "vente": VenteDetailSerializer(vente).data
            }, status=status.HTTP_200_OK)

        except Vente.DoesNotExist:
            return Response({"error": "Vente non trouvée"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def annuler(self, request, pk=None):
        """Annuler une vente"""
        try:
            vente = self.get_object()

            if vente.statut != 'brouillon':
                return Response(
                    {"error": "Seules les ventes en brouillon peuvent être annulées"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Libérer les stocks réservés
            for ligne in vente.lignes_vente.all():
                try:
                    stock_entrepot = StockEntrepot.objects.get(
                        produit=ligne.produit,
                        entrepot=ligne.entrepot
                    )
                    stock_entrepot.liberer_stock(ligne.quantite)
                except StockEntrepot.DoesNotExist:
                    pass

            # Annuler la vente
            vente.statut = 'annulee'
            vente.save()

            # Rafraîchir les données
            vente.refresh_from_db()

            return Response({
                "message": "Vente annulée avec succès",
                "vente": VenteDetailSerializer(vente).data
            }, status=status.HTTP_200_OK)

        except Vente.DoesNotExist:
            return Response({"error": "Vente non trouvée"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def enregistrer_paiement(self, request, pk=None):
        """Enregistrer un paiement pour une vente"""
        try:
            vente = self.get_object()

            # Vérifier que la vente est confirmée
            if vente.statut != 'confirmee':
                return Response(
                    {"error": "Seules les ventes confirmées peuvent recevoir des paiements"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Vérifier que la vente n'est pas déjà entièrement payée
            if vente.statut_paiement == 'paye':
                return Response(
                    {"error": "Cette vente est déjà entièrement payée"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            serializer = EnregistrerPaiementSerializer(
                data=request.data,
                context={'vente': vente}
            )

            if serializer.is_valid():
                data = serializer.validated_data

                # Créer le paiement
                paiement = Paiement.objects.create(
                    vente=vente,
                    montant=data['montant'],
                    mode_paiement=data['mode_paiement'],
                    reference=data.get('reference', ''),
                    notes=data.get('notes', ''),
                    created_by=request.user
                )

                # Mettre à jour le montant payé de la vente
                vente.montant_paye += data['montant']

                # Si la vente n'a pas de mode de paiement principal, utiliser celui du premier paiement
                if not vente.mode_paiement:
                    vente.mode_paiement = data['mode_paiement']

                # Mettre à jour le statut de paiement
                if vente.montant_paye >= vente.montant_total:
                    vente.statut_paiement = 'paye'
                    vente.date_paiement = timezone.now()
                elif vente.montant_paye > 0:
                    vente.statut_paiement = 'partiel'

                # Si la vente a une date d'échéance et est en retard
                if vente.date_echeance and vente.statut_paiement != 'paye':
                    if timezone.now().date() > vente.date_echeance:
                        vente.statut_paiement = 'retard'

                vente.save()

                # Log d'audit
                AuditLog.objects.create(
                    user=request.user,
                    action='vente',
                    modele='Paiement',
                    objet_id=paiement.id,
                    details={
                        'vente': vente.numero_vente,
                        'montant': str(data['montant']),
                        'mode_paiement': data['mode_paiement'],
                        'nouveau_statut': vente.statut_paiement
                    }
                )

                return Response({
                    'message': 'Paiement enregistré avec succès',
                    'paiement': PaiementSerializer(paiement).data,
                    'vente': VenteDetailSerializer(vente).data
                }, status=status.HTTP_200_OK)

            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        except Vente.DoesNotExist:
            return Response({'error': 'Vente non trouvée'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def generer_facture(self, request, pk=None):
        """Générer une facture pour une vente"""
        try:
            vente = self.get_object()

            # Vérifier si une facture existe déjà
            if hasattr(vente, 'facture'):
                return Response({'error': 'Une facture existe déjà pour cette vente'}, status=400)

            # Générer numéro de facture
            today = timezone.now().strftime('%Y%m%d')
            last_facture_today = Facture.objects.filter(
                numero_facture__startswith=f'F{today}'
            ).count()
            numero_facture = f'F{today}{last_facture_today + 1:04d}'

            # Créer la facture (sans PDF pour l'instant)
            facture = Facture.objects.create(
                vente=vente,
                numero_facture=numero_facture,
                montant_ttc=vente.montant_total,
                montant_ht=vente.montant_total / 1.2,  # Exemple avec 20% TVA
                tva=20.0
            )

            return Response({
                'message': 'Facture générée avec succès',
                'facture': FactureSerializer(facture).data
            })

        except Vente.DoesNotExist:
            return Response({'error': 'Vente non trouvée'}, status=404)

    @action(detail=False, methods=['get'])
    def ventes_impayees(self, request):
        """Liste des ventes impayées ou partiellement payées"""
        queryset = self.get_queryset().filter(
            statut='confirmee',
            statut_paiement__in=['non_paye', 'partiel']
        ).order_by('date_echeance')

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = VenteDetailSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = VenteDetailSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def ventes_en_retard(self, request):
        """Liste des ventes en retard de paiement"""
        queryset = self.get_queryset().filter(
            statut='confirmee',
            statut_paiement__in=['non_paye', 'partiel', 'retard'],
            date_echeance__lt=timezone.now().date()
        ).order_by('date_echeance')

        # Calculer les jours de retard pour chaque vente
        result = []
        for vente in queryset:
            data = VenteDetailSerializer(vente).data
            data['jours_retard'] = vente.jours_retard(
            ) if hasattr(vente, 'jours_retard') else 0
            result.append(data)

        return Response(result)


class HistoriqueClientViewSet(viewsets.ViewSet):
    """API pour l'historique des factures d'un client"""
    permission_classes = [IsAdminOrVendeur]

    def list(self, request):
        client_id = request.query_params.get('client_id')

        if not client_id:
            return Response({'error': 'client_id est requis'}, status=400)

        try:
            client = Client.objects.get(id=client_id)
        except Client.DoesNotExist:
            return Response({'error': 'Client non trouvé'}, status=404)

        # Récupérer toutes les ventes du client
        ventes = Vente.objects.filter(
            client=client,
            statut='confirmee'
        ).order_by('-created_at')

        # Calculer les statistiques
        total_achats = ventes.aggregate(Sum('montant_total'))[
            'montant_total__sum'] or 0
        total_paye = ventes.aggregate(Sum('montant_paye'))[
            'montant_paye__sum'] or 0
        ventes_en_retard = ventes.filter(
            date_echeance__lt=timezone.now().date(),
            statut_paiement__in=['non_paye', 'partiel']
        ).count()

        dernier_achat = ventes.first().created_at if ventes.exists() else None

        # Pagination
        page = self.paginate_queryset(ventes)
        if page is not None:
            ventes_serializer = VenteDetailSerializer(page, many=True)

            return self.get_paginated_response({
                'client': ClientSerializer(client).data,
                'statistiques': {
                    'total_achats': total_achats,
                    'total_paye': total_paye,
                    'solde_restant': total_achats - total_paye,
                    'nombre_ventes': ventes.count(),
                    'ventes_en_retard': ventes_en_retard,
                    'dernier_achat': dernier_achat
                },
                'ventes': ventes_serializer.data
            })

        # Si pas de pagination
        ventes_serializer = VenteDetailSerializer(ventes, many=True)

        return Response({
            'client': ClientSerializer(client).data,
            'statistiques': {
                'total_achats': total_achats,
                'total_paye': total_paye,
                'solde_restant': total_achats - total_paye,
                'nombre_ventes': ventes.count(),
                'ventes_en_retard': ventes_en_retard,
                'dernier_achat': dernier_achat
            },
            'ventes': ventes_serializer.data
        })


class RapportPaiementsViewSet(viewsets.ViewSet):
    """Rapports sur les paiements"""
    permission_classes = [IsAdminOrVendeur]

    @action(detail=False, methods=['get'])
    def recouvrements(self, request):
        """Rapport de recouvrement"""
        date_debut = request.query_params.get('date_debut')
        date_fin = request.query_params.get('date_fin')

        paiements = Paiement.objects.all()

        if date_debut and date_fin:
            paiements = paiements.filter(
                date_paiement__date__gte=date_debut,
                date_paiement__date__lte=date_fin
            )

        # Regrouper par mode de paiement
        par_mode = paiements.values('mode_paiement').annotate(
            total=Sum('montant'),
            count=Count('id')
        )

        # Regrouper par jour
        par_jour = paiements.values('date_paiement__date').annotate(
            total=Sum('montant'),
            count=Count('id')
        ).order_by('date_paiement__date')

        # Montants impayés
        ventes_impayees = Vente.objects.filter(
            statut='confirmee',
            statut_paiement__in=['non_paye', 'partiel']
        )

        total_impaye = ventes_impayees.aggregate(
            total=Sum('montant_restant')
        )['total'] or 0

        return Response({
            'total_paiements': paiements.aggregate(Sum('montant'))['montant__sum'] or 0,
            'nombre_paiements': paiements.count(),
            'par_mode_paiement': list(par_mode),
            'par_jour': list(par_jour),
            'impayes': {
                'total': total_impaye,
                'nombre_ventes': ventes_impayees.count(),
                'ventes': VenteDetailSerializer(ventes_impayees[:20], many=True).data
            }
        })


class DashboardViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminOrVendeur]

    def list(self, request):
        user = request.user
        today = datetime.now().date()
        month_start = today.replace(day=1)
        week_start = today - timedelta(days=today.weekday())

        # Filtrage selon le rôle
        if user.role == 'admin':
            ventes_filter = Vente.objects.filter(statut='confirmee')
            clients_filter = Client.objects.all()
            entrepots_filter = Entrepot.objects.all()
        else:
            ventes_filter = Vente.objects.filter(
                created_by=user, statut='confirmee'
            )
            clients_filter = Client.objects.all()  # Vendeur voit tous les clients
            entrepots_filter = Entrepot.objects.filter(actif=True)  # Vendeur voit tous les entrepôts actifs

        # Statistiques générales
        total_ventes = ventes_filter.count()
        chiffre_affaires = ventes_filter.aggregate(Sum('montant_total'))[
            'montant_total__sum'] or 0
        total_clients = clients_filter.count()
        total_produits = Produit.objects.count()

        # Statistiques entrepôts
        total_entrepots = entrepots_filter.count()

        # Calculer la valeur totale des stocks par entrepôt
        valeur_stock_total = 0
        entrepots_stocks = []
        for entrepot in entrepots_filter:
            valeur_stock = entrepot.stock_total_valeur()
            valeur_stock_total += valeur_stock
            entrepots_stocks.append({
                'id': entrepot.id,
                'nom': entrepot.nom,
                'valeur_stock': float(valeur_stock),
                'produits_count': entrepot.produits_count(),
                'statut': 'actif' if entrepot.actif else 'inactif'
            })

        # Ventes du mois
        ventes_mois = ventes_filter.filter(created_at__gte=month_start).aggregate(
            Sum('montant_total'))['montant_total__sum'] or 0

        # Ventes de la semaine
        ventes_semaine = ventes_filter.filter(created_at__gte=week_start).aggregate(
            Sum('montant_total'))['montant_total__sum'] or 0

        # Produits en stock faible (par entrepôt)
        produits_low_stock = []
        stocks_faibles = StockEntrepot.objects.filter(
            quantite_disponible__gt=0,
            quantite_disponible__lte=models.F('stock_alerte')
        ).select_related('produit', 'entrepot')

        for stock in stocks_faibles[:10]:  # Limiter à 10 résultats
            produits_low_stock.append({
                'id': stock.produit.id,
                'nom': stock.produit.nom,
                'code': stock.produit.code,
                'entrepot_id': stock.entrepot.id,
                'entrepot_nom': stock.entrepot.nom,
                'stock_actuel': stock.quantite_disponible,
                'stock_alerte': stock.stock_alerte,
                'statut': 'faible'
            })

        # Dernières ventes
        dernieres_ventes = ventes_filter.order_by('-created_at')[:5]
        ventes_serializer = VenteSerializer(dernieres_ventes, many=True)

        # Top produits vendus
        top_produits = Produit.objects.filter(
            lignedevente__vente__in=ventes_filter.filter(
                created_at__gte=month_start
            )
        ).annotate(
            total_vendu=Sum('lignedevente__quantite')
        ).order_by('-total_vendu')[:5]

        top_produits_data = []
        for produit in top_produits:
            top_produits_data.append({
                'id': produit.id,
                'nom': produit.nom,
                'total_vendu': produit.total_vendu or 0
            })

        return Response({
            'stats': {
                'total_ventes': total_ventes,
                'chiffre_affaires': float(chiffre_affaires),
                'chiffre_affaires_mois': float(ventes_mois),
                'chiffre_affaires_semaine': float(ventes_semaine),
                'total_clients': total_clients,
                'total_produits': total_produits,
                'total_entrepots': total_entrepots,
                'valeur_stock_total': float(valeur_stock_total),
            },
            'entrepots': entrepots_stocks,
            'produits_low_stock': produits_low_stock,
            'top_produits': top_produits_data,
            'dernieres_ventes': ventes_serializer.data
        })


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [IsAdmin]

    def get_queryset(self):
        queryset = AuditLog.objects.all().order_by('-created_at')

        # Filtre par recherche
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(user__email__icontains=search) |
                Q(modele__icontains=search) |
                Q(action__icontains=search) |
                Q(details__icontains=search)
            )

        # Filtre par action
        action = self.request.query_params.get('action')
        if action:
            queryset = queryset.filter(action=action)

        # Filtre par modèle
        modele = self.request.query_params.get('modele')
        if modele:
            queryset = queryset.filter(modele=modele)

        # Filtre par date
        date_debut = self.request.query_params.get('date_debut')
        date_fin = self.request.query_params.get('date_fin')

        if date_debut:
            queryset = queryset.filter(created_at__date__gte=date_debut)
        if date_fin:
            queryset = queryset.filter(created_at__date__lte=date_fin)

        # Filtre par entrepôt
        entrepot_id = self.request.query_params.get('entrepot')
        if entrepot_id:
            queryset = queryset.filter(
                Q(modele='MouvementStock', details__icontains=f'"entrepot_id": {entrepot_id}') |
                Q(modele='Vente',
                  details__icontains=f'"entrepots": ["{entrepot_id}"')
            )

        return queryset.select_related('user')


class RapportsViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminOrVendeur]

    @action(detail=False, methods=['get'])
    def ventes(self, request):
        """Rapport détaillé des ventes"""
        user = request.user
        date_debut = request.query_params.get('date_debut')
        date_fin = request.query_params.get('date_fin')
        categorie_id = request.query_params.get('categorie')
        vendeur_id = request.query_params.get('vendeur')
        entrepot_id = request.query_params.get('entrepot')

        # Filtrage de base selon le rôle
        if user.role == 'admin':
            queryset = Vente.objects.filter(statut='confirmee')
        else:
            queryset = Vente.objects.filter(
                statut='confirmee',
                created_by=user
            )

        if date_debut and date_fin:
            queryset = queryset.filter(
                created_at__date__gte=date_debut,
                created_at__date__lte=date_fin
            )

        if vendeur_id and user.role == 'admin':  # Seul l'admin peut filtrer par vendeur
            queryset = queryset.filter(created_by_id=vendeur_id)

        if entrepot_id:
            queryset = queryset.filter(
                lignes_vente__entrepot_id=entrepot_id
            ).distinct()

        # Filtrage par catégorie
        if categorie_id:
            queryset = queryset.filter(
                lignes_vente__produit__categorie_id=categorie_id
            ).distinct()

        # Statistiques
        stats = {
            'total_ventes': queryset.count(),
            'chiffre_affaires_total': queryset.aggregate(
                total=Sum('montant_total')
            )['total'] or 0,
            'clients_actifs': Client.objects.filter(
                vente__in=queryset
            ).distinct().count(),
            'total_produits_vendus': LigneDeVente.objects.filter(
                vente__in=queryset
            ).aggregate(total=Sum('quantite'))['total'] or 0,
        }

        # Top vendeur (seulement pour admin)
        if user.role == 'admin':
            top_vendeur = User.objects.filter(
                vente__in=queryset
            ).annotate(
                total_ventes=Count('vente')
            ).order_by('-total_ventes').first()

            stats['top_vendeur'] = {
                'id': top_vendeur.id if top_vendeur else None,
                'email': top_vendeur.email if top_vendeur else 'N/A',
                'total_ventes': top_vendeur.total_ventes if top_vendeur else 0
            }
        else:
            stats['top_vendeur'] = {
                'id': user.id,
                'email': user.email,
                'total_ventes': queryset.count()
            }

        # Top produit
        top_produit = Produit.objects.filter(
            lignedevente__vente__in=queryset
        ).annotate(
            total_vendu=Sum('lignedevente__quantite')
        ).order_by('-total_vendu').first()

        stats['top_produit'] = {
            'id': top_produit.id if top_produit else None,
            'nom': top_produit.nom if top_produit else 'N/A',
            'total_vendu': top_produit.total_vendu if top_produit else 0
        }

        # Top entrepôt
        top_entrepot = Entrepot.objects.filter(
            lignes_vente__vente__in=queryset
        ).annotate(
            total_ventes=Count('lignedevente__vente', distinct=True)
        ).order_by('-total_ventes').first()

        stats['top_entrepot'] = {
            'id': top_entrepot.id if top_entrepot else None,
            'nom': top_entrepot.nom if top_entrepot else 'N/A',
            'total_ventes': top_entrepot.total_ventes if top_entrepot else 0
        }

        # Ventes détaillées
        ventes_detaillees = VenteSerializer(
            queryset.order_by('-created_at')[:50],  # Limiter à 50 résultats
            many=True
        ).data

        return Response({
            'stats': stats,
            'ventes_detaillees': ventes_detaillees
        })

    @action(detail=False, methods=['get'])
    def stocks(self, request):
        """Rapport sur l'état des stocks par entrepôt"""
        entrepot_id = request.query_params.get('entrepot')

        if entrepot_id:
            stocks = StockEntrepot.objects.filter(entrepot_id=entrepot_id)
        else:
            stocks = StockEntrepot.objects.all()

        stocks = stocks.select_related(
            'produit', 'entrepot', 'produit__categorie')

        produits_data = []
        for stock in stocks:
            statut = 'normal'
            if stock.en_rupture:
                statut = 'rupture'
            elif stock.stock_faible:
                statut = 'faible'

            produits_data.append({
                'id': stock.produit.id,
                'nom': stock.produit.nom,
                'code': stock.produit.code,
                'categorie_nom': stock.produit.categorie.nom if stock.produit.categorie else 'N/A',
                'entrepot_id': stock.entrepot.id,
                'entrepot_nom': stock.entrepot.nom,
                'stock_actuel': stock.quantite_disponible,
                'stock_total': stock.quantite,
                'stock_reserve': stock.quantite_reservee,
                'stock_alerte': stock.stock_alerte,
                'statut': statut,
                'prix_achat': stock.produit.prix_achat,
                'prix_vente': stock.produit.prix_vente,
            })

        return Response({
            'produits_stock': produits_data
        })

    @action(detail=False, methods=['get'])
    def clients(self, request):
        """Rapport sur les clients"""
        user = request.user
        date_debut = request.query_params.get('date_debut')
        date_fin = request.query_params.get('date_fin')

        # Les vendeurs voient tous les clients
        clients = Client.objects.all()

        clients_data = []
        for client in clients:
            # Calculer les statistiques client
            if user.role == 'admin':
                ventes_client = Vente.objects.filter(
                    client=client,
                    statut='confirmee'
                )
            else:
                ventes_client = Vente.objects.filter(
                    client=client,
                    statut='confirmee',
                    created_by=user
                )

            if date_debut and date_fin:
                ventes_client = ventes_client.filter(
                    created_at__date__gte=date_debut,
                    created_at__date__lte=date_fin
                )

            total_achats = ventes_client.aggregate(
                total=Sum('montant_total')
            )['total'] or 0

            clients_data.append({
                'id': client.id,
                'nom': client.nom,
                'type_client': client.type_client,
                'telephone': client.telephone,
                'email': client.email,
                'total_achats': float(total_achats),
                'nombre_commandes': ventes_client.count(),
            })

        return Response({
            'clients': clients_data
        })

    @action(detail=False, methods=['get'])
    def mouvements_stock(self, request):
        """Rapport des mouvements de stock"""
        user = request.user
        date_debut = request.query_params.get('date_debut')
        date_fin = request.query_params.get('date_fin')
        entrepot_id = request.query_params.get('entrepot')
        type_mouvement = request.query_params.get('type_mouvement')

        # Filtrage selon le rôle
        if user.role == 'admin':
            mouvements = MouvementStock.objects.all()
        else:
            mouvements = MouvementStock.objects.filter(created_by=user)

        mouvements = mouvements.select_related(
            'produit', 'created_by', 'entrepot')

        if date_debut and date_fin:
            mouvements = mouvements.filter(
                created_at__date__gte=date_debut,
                created_at__date__lte=date_fin
            )

        if entrepot_id:
            mouvements = mouvements.filter(entrepot_id=entrepot_id)

        if type_mouvement:
            mouvements = mouvements.filter(type_mouvement=type_mouvement)

        mouvements_data = MouvementStockSerializer(
            mouvements.order_by('-created_at'),
            many=True
        ).data

        return Response({
            'mouvements': mouvements_data
        })

    @action(detail=False, methods=['get'])
    def entrepots(self, request):
        """Rapport sur les entrepôts"""
        user = request.user
        
        # Les vendeurs voient tous les entrepôts actifs
        if user.role == 'admin':
            entrepots = Entrepot.objects.all()
        else:
            entrepots = Entrepot.objects.filter(actif=True)

        entrepots_data = []
        for entrepot in entrepots:
            # Statistiques de l'entrepôt
            stocks = StockEntrepot.objects.filter(entrepot=entrepot)
            valeur_stock = entrepot.stock_total_valeur()

            # Ventes depuis cet entrepôt
            if user.role == 'admin':
                ventes_entrepot = Vente.objects.filter(
                    lignes_vente__entrepot=entrepot,
                    statut='confirmee'
                ).distinct()
            else:
                ventes_entrepot = Vente.objects.filter(
                    lignes_vente__entrepot=entrepot,
                    statut='confirmee',
                    created_by=user
                ).distinct()

            chiffre_affaires = ventes_entrepot.aggregate(
                total=Sum('montant_total')
            )['total'] or 0

            entrepots_data.append({
                'id': entrepot.id,
                'nom': entrepot.nom,
                'responsable': entrepot.responsable.email if entrepot.responsable else 'N/A',
                'valeur_stock': float(valeur_stock),
                'nombre_produits': stocks.count(),
                'chiffre_affaires': float(chiffre_affaires),
                'nombre_ventes': ventes_entrepot.count(),
                'statut': 'actif' if entrepot.actif else 'inactif'
            })

        return Response({
            'entrepots': entrepots_data
        })


# Vue pour les statistiques avancées
class StatistiquesViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminOrVendeur]

    @action(detail=False, methods=['get'])
    def evolution_ventes(self, request):
        """Évolution des ventes sur les 30 derniers jours"""
        user = request.user
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=30)

        if user.role == 'admin':
            ventes = Vente.objects.filter(
                statut='confirmee',
                created_at__date__gte=start_date,
                created_at__date__lte=end_date
            )
        else:
            ventes = Vente.objects.filter(
                created_by=user,
                statut='confirmee',
                created_at__date__gte=start_date,
                created_at__date__lte=end_date
            )

        # Grouper par jour
        jours = {}
        current_date = start_date
        while current_date <= end_date:
            jours[current_date.strftime('%Y-%m-%d')] = {
                'date': current_date.strftime('%d/%m'),
                'ventes': 0,
                'chiffre_affaires': 0
            }
            current_date += timedelta(days=1)

        for vente in ventes:
            date_str = vente.created_at.date().strftime('%Y-%m-%d')
            if date_str in jours:
                jours[date_str]['ventes'] += 1
                jours[date_str]['chiffre_affaires'] += float(
                    vente.montant_total)

        return Response({
            'periode': {
                'debut': start_date.strftime('%d/%m/%Y'),
                'fin': end_date.strftime('%d/%m/%Y')
            },
            'evolution': list(jours.values())
        })

    @action(detail=False, methods=['get'])
    def produits_populaires(self, request):
        """Produits les plus vendus"""
        user = request.user
        days = int(request.query_params.get('days', 30))

        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)

        if user.role == 'admin':
            ventes = Vente.objects.filter(
                statut='confirmee',
                created_at__date__gte=start_date
            )
        else:
            ventes = Vente.objects.filter(
                created_by=user,
                statut='confirmee',
                created_at__date__gte=start_date
            )

        # Produits les plus vendus
        produits = Produit.objects.filter(
            lignedevente__vente__in=ventes
        ).annotate(
            total_vendu=Sum('lignedevente__quantite'),
            chiffre_affaires=Sum('lignedevente__quantite') *
            models.F('prix_vente')
        ).order_by('-total_vendu')[:10]

        data = []
        for produit in produits:
            data.append({
                'id': produit.id,
                'nom': produit.nom,
                'code': produit.code,
                'total_vendu': produit.total_vendu or 0,
                'chiffre_affaires': float(produit.chiffre_affaires or 0)
            })

        return Response({
            'periode': f"{days} derniers jours",
            'produits': data
        })


# Vue pour les opérations de stock avancées
class StockOperationsViewSet(viewsets.ViewSet):
    permission_classes = [IsAdmin]

    @action(detail=False, methods=['post'])
    def ajuster_stock(self, request):
        """Ajustement manuel du stock"""
        from rest_framework import serializers

        class AjustementSerializer(serializers.Serializer):
            entrepot = serializers.PrimaryKeyRelatedField(
                queryset=Entrepot.objects.all())
            produit = serializers.PrimaryKeyRelatedField(
                queryset=Produit.objects.all())
            quantite = serializers.IntegerField(min_value=1)
            motif = serializers.CharField(max_length=500)
            type_ajustement = serializers.ChoiceField(
                choices=['ajout', 'retrait'])

        serializer = AjustementSerializer(data=request.data)

        if serializer.is_valid():
            data = serializer.validated_data

            try:
                stock, created = StockEntrepot.objects.get_or_create(
                    entrepot=data['entrepot'],
                    produit=data['produit'],
                    defaults={'quantite': 0}
                )

                ancienne_quantite = stock.quantite

                if data['type_ajustement'] == 'ajout':
                    stock.quantite += data['quantite']
                else:  # retrait
                    stock.quantite = max(0, stock.quantite - data['quantite'])

                stock.save()

                # Créer un mouvement de stock
                MouvementStock.objects.create(
                    produit=data['produit'],
                    type_mouvement='ajustement',
                    quantite=data['quantite'],
                    prix_unitaire=data['produit'].prix_achat,
                    motif=data['motif'],
                    entrepot=data['entrepot'],
                    created_by=request.user
                )

                # Log d'audit
                AuditLog.objects.create(
                    user=request.user,
                    action='modification',
                    modele='StockEntrepot',
                    objet_id=stock.id,
                    details={
                        'entrepot': data['entrepot'].nom,
                        'produit': data['produit'].nom,
                        'ancienne_quantite': ancienne_quantite,
                        'nouvelle_quantite': stock.quantite,
                        'motif': data['motif']
                    }
                )

                return Response({
                    'message': 'Stock ajusté avec succès',
                    'ancienne_quantite': ancienne_quantite,
                    'nouvelle_quantite': stock.quantite
                })

            except Exception as e:
                return Response({'error': str(e)}, status=400)

        return Response(serializer.errors, status=400)

    @action(detail=False, methods=['post'])
    def initialiser_stock(self, request):
        """Initialiser le stock d'un produit dans un entrepôt"""
        from rest_framework import serializers

        class InitialisationSerializer(serializers.Serializer):
            entrepot = serializers.PrimaryKeyRelatedField(
                queryset=Entrepot.objects.all())
            produit = serializers.PrimaryKeyRelatedField(
                queryset=Produit.objects.all())
            quantite = serializers.IntegerField(min_value=0)
            emplacement = serializers.CharField(
                required=False, allow_blank=True)

        serializer = InitialisationSerializer(data=request.data)

        if serializer.is_valid():
            data = serializer.validated_data

            try:
                stock, created = StockEntrepot.objects.get_or_create(
                    entrepot=data['entrepot'],
                    produit=data['produit'],
                    defaults={
                        'quantite': data['quantite'],
                        'emplacement': data.get('emplacement', '')
                    }
                )

                if not created:
                    stock.quantite = data['quantite']
                    if 'emplacement' in data:
                        stock.emplacement = data['emplacement']
                    stock.save()

                # Créer un mouvement de stock
                MouvementStock.objects.create(
                    produit=data['produit'],
                    type_mouvement='entree',
                    quantite=data['quantite'],
                    prix_unitaire=data['produit'].prix_achat,
                    motif='Initialisation du stock',
                    entrepot=data['entrepot'],
                    created_by=request.user
                )

                # Log d'audit
                AuditLog.objects.create(
                    user=request.user,
                    action='creation' if created else 'modification',
                    modele='StockEntrepot',
                    objet_id=stock.id,
                    details={
                        'entrepot': data['entrepot'].nom,
                        'produit': data['produit'].nom,
                        'quantite': data['quantite'],
                        'emplacement': data.get('emplacement', ''),
                        'action': 'initialisation_stock'
                    }
                )

                return Response({
                    'message': 'Stock initialisé avec succès',
                    'created': created,
                    'quantite': stock.quantite
                })

            except Exception as e:
                return Response({'error': str(e)}, status=400)

        return Response(serializer.errors, status=400)