from django.utils import timezone
from rest_framework import viewsets, status, permissions
from rest_framework.response import Response
from rest_framework.decorators import action
from django.contrib.auth import get_user_model, authenticate
from knox.models import AuthToken
from django.db import transaction
from django.db.models import Sum, Q, Count
from datetime import datetime, timedelta
from django.http import HttpResponse
import csv

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

            if user.is_superuser:
                return Response({"error": "Cannot delete super user"}, status=400)

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
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    def get_queryset(self):
        queryset = Produit.objects.all()

        categorie_id = self.request.query_params.get('categorie')
        if categorie_id:
            queryset = queryset.filter(categorie_id=categorie_id)

        low_stock = self.request.query_params.get('low_stock')
        if low_stock:
            produits_ids = [p.id for p in queryset if p.stock_faible]
            queryset = queryset.filter(id__in=produits_ids)

        out_of_stock = self.request.query_params.get('out_of_stock')
        if out_of_stock:
            produits_ids = [p.id for p in queryset if p.en_rupture]
            queryset = queryset.filter(id__in=produits_ids)

        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class ClientViewSet(viewsets.ModelViewSet):
    serializer_class = ClientSerializer
    permission_classes = [IsAdminOrVendeur]

    def get_queryset(self):
        return Client.objects.all()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class MouvementStockViewSet(viewsets.ModelViewSet):
    serializer_class = MouvementStockSerializer
    permission_classes = [IsAdmin]

    def get_queryset(self):
        queryset = MouvementStock.objects.all().order_by('-created_at')

        entrepot_id = self.request.query_params.get('entrepot')
        if entrepot_id:
            queryset = queryset.filter(entrepot_id=entrepot_id)

        produit_id = self.request.query_params.get('produit')
        if produit_id:
            queryset = queryset.filter(produit_id=produit_id)

        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class EntrepotViewSet(viewsets.ModelViewSet):
    serializer_class = EntrepotSerializer
    permission_classes = [IsAdminOrVendeur]

    def get_queryset(self):
        return Entrepot.objects.filter(actif=True)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class StockEntrepotViewSet(viewsets.ModelViewSet):
    serializer_class = StockEntrepotSerializer
    permission_classes = [IsAdminOrVendeur]

    def get_queryset(self):
        queryset = StockEntrepot.objects.all()

        entrepot_id = self.request.query_params.get('entrepot')
        if entrepot_id:
            queryset = queryset.filter(entrepot_id=entrepot_id)

        produit_id = self.request.query_params.get('produit')
        if produit_id:
            queryset = queryset.filter(produit_id=produit_id)

        low_stock = self.request.query_params.get('low_stock')
        if low_stock:
            queryset = queryset.filter(
                quantite__gt=models.F('quantite_reservee'),
                quantite__lte=models.F(
                    'quantite_reservee') + models.F('stock_alerte')
            )

        out_of_stock = self.request.query_params.get('out_of_stock')
        if out_of_stock:
            queryset = queryset.filter(
                quantite__lte=models.F('quantite_reservee')
            )

        return queryset

    @action(detail=False, methods=['get'])
    def stock_global(self, request):
        entrepot_id = request.query_params.get('entrepot')

        if entrepot_id:
            stocks = StockEntrepot.objects.filter(entrepot_id=entrepot_id)
        else:
            stocks = StockEntrepot.objects.all()

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
    permission_classes = [IsAdminOrVendeur]

    def list(self, request):
        produit_id = request.query_params.get('produit')

        if not produit_id:
            return Response({'error': 'Paramètre produit requis'}, status=400)

        try:
            produit = Produit.objects.get(id=produit_id)
        except Produit.DoesNotExist:
            return Response({'error': 'Produit non trouvé'}, status=404)

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


class StockDetailViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminOrVendeur]

    def list(self, request):
        produit_id = request.query_params.get('produit')
        entrepot_id = request.query_params.get('entrepot')

        if not produit_id or not entrepot_id:
            return Response({'error': 'Paramètres produit et entrepot requis'}, status=400)

        try:
            stock = StockEntrepot.objects.get(
                produit_id=produit_id,
                entrepot_id=entrepot_id
            )

            serializer = StockDetailSerializer(stock)
            return Response(serializer.data)

        except StockEntrepot.DoesNotExist:
            return Response({
                'error': 'Stock non trouvé',
                'produit_id': produit_id,
                'entrepot_id': entrepot_id
            }, status=404)
        except Exception as e:
            return Response({'error': str(e)}, status=400)


class StockVerificationViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminOrVendeur]

    @action(detail=False, methods=['post'])
    def verifier_stock(self, request):
        serializer = StockVerificationSerializer(data=request.data)

        if serializer.is_valid():
            data = serializer.validated_data

            try:
                stock = StockEntrepot.objects.get(
                    produit_id=data['produit_id'],
                    entrepot_id=data['entrepot_id']
                )

                disponible = stock.quantite_disponible
                suffisant = disponible >= data['quantite']

                return Response({
                    'suffisant': suffisant,
                    'quantite_disponible': disponible,
                    'quantite_demandee': data['quantite'],
                    'quantite_totale': stock.quantite,
                    'quantite_reservee': stock.quantite_reservee,
                    'stock_alerte': stock.stock_alerte,
                    'message': f'Stock {"suffisant" if suffisant else "insuffisant"}: {disponible} unités disponibles' if suffisant else f'Stock insuffisant: {disponible} unités disponibles, besoin: {data["quantite"]}'
                })

            except StockEntrepot.DoesNotExist:
                return Response({
                    'suffisant': False,
                    'quantite_disponible': 0,
                    'quantite_demandee': data['quantite'],
                    'message': 'Produit non disponible dans cet entrepôt'
                }, status=404)

        return Response(serializer.errors, status=400)


class TransfertEntrepotViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'create':
            return TransfertEntrepotCreateSerializer
        return TransfertEntrepotSerializer

    def get_queryset(self):
        queryset = TransfertEntrepot.objects.all().order_by('-created_at')

        statut = self.request.query_params.get('statut')
        if statut:
            queryset = queryset.filter(statut=statut)

        return queryset

    def perform_create(self, serializer):
        serializer.save()

    @action(detail=True, methods=['post'])
    def confirmer(self, request, pk=None):
        try:
            transfert = self.get_object()

            if transfert.statut != 'brouillon':
                return Response(
                    {"detail": "Seuls les transferts en brouillon peuvent être confirmés."},
                    status=status.HTTP_400_BAD_REQUEST
                )

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

        # Filtres existants...
        statut = self.request.query_params.get('statut')
        if statut:
            queryset = queryset.filter(statut=statut)

        statut_paiement = self.request.query_params.get('statut_paiement')
        if statut_paiement:
            queryset = queryset.filter(statut_paiement=statut_paiement)

        client_id = self.request.query_params.get('client')
        if client_id:
            queryset = queryset.filter(client_id=client_id)

        type_vente = self.request.query_params.get('type_vente')
        if type_vente:
            queryset = queryset.filter(type_vente=type_vente)

        type_reduction = self.request.query_params.get('type_reduction')
        if type_reduction:
            queryset = queryset.filter(type_reduction=type_reduction)

        date_debut = self.request.query_params.get('date_debut')
        date_fin = self.request.query_params.get('date_fin')
        if date_debut and date_fin:
            queryset = queryset.filter(
                created_at__date__gte=date_debut,
                created_at__date__lte=date_fin
            )

        en_retard = self.request.query_params.get('en_retard')
        if en_retard and en_retard.lower() == 'true':
            queryset = queryset.filter(
                date_echeance__lt=timezone.now().date(),
                statut_paiement__in=['non_paye', 'partiel']
            )

        # Filtre par utilisateur si pas admin
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

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def create(self, request, *args, **kwargs):
        """Création d'une vente avec réduction générale"""
        try:
            with transaction.atomic():
                # Valider et créer la vente via serializer
                serializer = self.get_serializer(data=request.data)
                serializer.is_valid(raise_exception=True)

                # Sauvegarder la vente
                self.perform_create(serializer)
                vente = serializer.instance

                # Réserver le stock
                stocks_reserves = []
                for ligne in vente.lignes_vente.all():
                    try:
                        stock_entrepot = StockEntrepot.objects.get(
                            produit=ligne.produit,
                            entrepot=ligne.entrepot
                        )

                        stock_disponible = stock_entrepot.quantite - stock_entrepot.quantite_reservee

                        if ligne.quantite > stock_disponible:
                            raise serializers.ValidationError({
                                'lignes_vente': f'Stock insuffisant pour {ligne.produit.nom} dans {ligne.entrepot.nom}. Disponible: {stock_disponible}'
                            })

                        ancienne_reserve = stock_entrepot.quantite_reservee
                        stock_entrepot.quantite_reservee += ligne.quantite
                        stock_entrepot.save()

                        stocks_reserves.append({
                            'produit': ligne.produit.nom,
                            'entrepot': ligne.entrepot.nom,
                            'quantite': ligne.quantite,
                            'ancienne_reserve': ancienne_reserve,
                            'nouvelle_reserve': stock_entrepot.quantite_reservee,
                            'stock_total': stock_entrepot.quantite
                        })

                    except StockEntrepot.DoesNotExist:
                        raise serializers.ValidationError({
                            'lignes_vente': f'Stock non trouvé pour {ligne.produit.nom} dans {ligne.entrepot.nom}'
                        })

                # Log d'audit
                AuditLog.objects.create(
                    user=request.user,
                    action='creation',
                    modele='Vente',
                    objet_id=vente.id,
                    details={
                        'numero_vente': vente.numero_vente,
                        'type_reduction': vente.type_reduction,
                        'valeur_reduction': str(vente.valeur_reduction),
                        'montant_reduction': str(vente.montant_reduction),
                        'montant_total': str(vente.montant_total),
                        'stocks_reserves': stocks_reserves,
                        'statut': vente.statut
                    }
                )

                # Retourner la réponse
                response_serializer = VenteDetailSerializer(vente)

                return Response(
                    {
                        'message': 'Vente créée avec succès - Stock réservé',
                        'vente': response_serializer.data,
                        'reduction_info': {
                            'type': vente.type_reduction,
                            'valeur': vente.valeur_reduction,
                            'montant': vente.montant_reduction,
                            'montant_avant_reduction': vente.montant_avant_reduction,
                            'montant_apres_reduction': vente.montant_total
                        },
                        'stocks_reserves': stocks_reserves
                    },
                    status=status.HTTP_201_CREATED
                )

        except serializers.ValidationError as e:
            return Response(
                {"error": e.detail},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": f"Erreur interne: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def update(self, request, *args, **kwargs):
        """
        Mise à jour d'une vente avec ajustement des réductions
        """
        try:
            with transaction.atomic():
                instance = self.get_object()

                # Vérifier les permissions
                if request.user.role != 'admin' and instance.created_by != request.user:
                    return Response(
                        {"error": "Vous ne pouvez modifier que vos propres ventes"},
                        status=status.HTTP_403_FORBIDDEN
                    )

                # Vérifier que la vente est en brouillon
                if instance.statut != 'brouillon':
                    return Response(
                        {"error": "Impossible de modifier une vente confirmée"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Sauvegarder les anciennes lignes
                anciennes_lignes_dict = {}
                for ligne in instance.lignes_vente.all():
                    key = (ligne.produit_id, ligne.entrepot_id)
                    anciennes_lignes_dict[key] = {
                        'quantite': ligne.quantite,
                        'stock_preleve': ligne.stock_preleve,
                        'id': ligne.id
                    }

                # Mettre à jour la vente
                serializer = self.get_serializer(
                    instance,
                    data=request.data,
                    partial=kwargs.get('partial', False)
                )
                serializer.is_valid(raise_exception=True)

                # Récupérer les nouvelles valeurs de réduction
                nouvelle_reduction_type = request.data.get('type_reduction')
                nouvelle_valeur_reduction = request.data.get(
                    'valeur_reduction')

                if nouvelle_reduction_type is not None:
                    instance.type_reduction = nouvelle_reduction_type
                if nouvelle_valeur_reduction is not None:
                    instance.valeur_reduction = nouvelle_valeur_reduction

                self.perform_update(serializer)

                # Sauvegarder pour calculer les nouveaux totaux
                instance.save()

                # ... (gestion des ajustements de stock inchangée) ...

                # Informations sur la nouvelle réduction
                reduction_info = {
                    'type': instance.type_reduction,
                    'valeur': instance.valeur_reduction,
                    'montant': instance.montant_reduction,
                    'montant_avant_reduction': instance.montant_avant_reduction,
                    'montant_apres_reduction': instance.montant_total,
                    'pourcentage_effectif': instance.pourcentage_reduction
                }

                return Response({
                    'message': 'Vente mise à jour avec succès',
                    'vente': serializer.data,
                    'reduction_info': reduction_info,
                    # ... (autres informations) ...
                })

        except serializers.ValidationError as e:
            return Response(
                {"error": e.detail},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['post'])
    def confirmer(self, request, pk=None):
        """Confirmer la vente"""
        try:
            with transaction.atomic():
                vente = self.get_object()

                if vente.statut != 'brouillon':
                    return Response(
                        {"error": "Seules les ventes en brouillon peuvent être confirmées"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                if not vente.lignes_vente.exists():
                    return Response(
                        {"error": "La vente ne contient aucun produit"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # S'assurer que les totaux sont à jour
                vente.calculer_total()
                vente.save()

                vente.confirmer_vente()

                vente.refresh_from_db()

                # Informations sur la réduction
                reduction_info = {
                    'type': vente.type_reduction,
                    'valeur': vente.valeur_reduction,
                    'montant': vente.montant_reduction,
                    'montant_avant_reduction': vente.montant_avant_reduction,
                    'montant_apres_reduction': vente.montant_total,
                    'pourcentage_effectif': vente.pourcentage_reduction
                }

                return Response({
                    "message": "Vente confirmée avec succès",
                    "vente": VenteDetailSerializer(vente).data,
                    "reduction_info": reduction_info
                }, status=status.HTTP_200_OK)

        except Vente.DoesNotExist:
            return Response({"error": "Vente non trouvée"}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": f"Erreur interne: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'])
    def statistiques_reductions(self, request):
        """Statistiques sur les réductions appliquées"""
        user = request.user
        date_debut = request.query_params.get('date_debut')
        date_fin = request.query_params.get('date_fin')

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

        # Statistiques par type de réduction
        stats_par_type = queryset.values('type_reduction').annotate(
            nombre_ventes=Count('id'),
            total_reduction=Sum('montant_reduction'),
            total_ventes=Sum('montant_total')
        ).order_by('type_reduction')

        # Top 10 ventes avec les plus grosses réductions
        top_reductions = queryset.filter(
            type_reduction__in=['pourcentage', 'montant']
        ).order_by('-montant_reduction')[:10]

        top_reductions_data = []
        for vente in top_reductions:
            top_reductions_data.append({
                'id': vente.id,
                'numero_vente': vente.numero_vente,
                'client': vente.client.nom if vente.client else 'N/A',
                'type_reduction': vente.get_type_reduction_display(),
                'valeur_reduction': vente.valeur_reduction,
                'montant_reduction': vente.montant_reduction,
                'montant_total': vente.montant_total,
                'date': vente.created_at.strftime('%d/%m/%Y')
            })

        # Total des réductions
        total_reductions = queryset.filter(
            type_reduction__in=['pourcentage', 'montant']
        ).aggregate(total=Sum('montant_reduction'))['total'] or 0

        total_ventes = queryset.aggregate(
            total=Sum('montant_total'))['total'] or 0
        total_avant_reduction = total_ventes + total_reductions

        return Response({
            'stats_par_type': list(stats_par_type),
            'top_reductions': top_reductions_data,
            'totaux': {
                'total_reductions': float(total_reductions),
                'total_ventes': float(total_ventes),
                'total_avant_reduction': float(total_avant_reduction),
                'pourcentage_moyen_reduction': (total_reductions / total_avant_reduction * 100)
                if total_avant_reduction > 0 else 0
            }
        })


class HistoriqueClientViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminOrVendeur]

    def list(self, request):
        client_id = request.query_params.get('client_id')

        if not client_id:
            return Response({'error': 'client_id est requis'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            client = Client.objects.get(id=client_id)
        except Client.DoesNotExist:
            return Response({'error': 'Client non trouvé'}, status=status.HTTP_404_NOT_FOUND)

        ventes = Vente.objects.filter(
            client=client,
            statut='confirmee'
        ).order_by('-created_at')

        total_achats = ventes.aggregate(Sum('montant_total'))[
            'montant_total__sum'] or 0
        total_paye = ventes.aggregate(Sum('montant_paye'))[
            'montant_paye__sum'] or 0
        ventes_en_retard = ventes.filter(
            date_echeance__lt=timezone.now().date(),
            statut_paiement__in=['non_paye', 'partiel']
        ).count()

        dernier_achat = None
        if ventes.exists():
            premiere_vente = ventes.first()
            if premiere_vente:
                dernier_achat = premiere_vente.created_at

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))

        start_index = (page - 1) * page_size
        end_index = start_index + page_size

        ventes_paginees = ventes[start_index:end_index]

        ventes_serializer = VenteDetailSerializer(ventes_paginees, many=True)

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
            'ventes': ventes_serializer.data,
            'count': ventes.count(),
            'page': page,
            'page_size': page_size,
            'total_pages': (ventes.count() + page_size - 1) // page_size
        })


class RapportPaiementsViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminOrVendeur]

    @action(detail=False, methods=['get'])
    def recouvrements(self, request):
        date_debut = request.query_params.get('date_debut')
        date_fin = request.query_params.get('date_fin')

        paiements = Paiement.objects.all()

        if date_debut and date_fin:
            paiements = paiements.filter(
                date_paiement__date__gte=date_debut,
                date_paiement__date__lte=date_fin
            )

        par_mode = paiements.values('mode_paiement').annotate(
            total=Sum('montant'),
            count=Count('id')
        )

        par_jour = paiements.values('date_paiement__date').annotate(
            total=Sum('montant'),
            count=Count('id')
        ).order_by('date_paiement__date')

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

        if user.role == 'admin':
            ventes_filter = Vente.objects.filter(statut='confirmee')
            clients_filter = Client.objects.all()
            entrepots_filter = Entrepot.objects.all()
        else:
            ventes_filter = Vente.objects.filter(
                created_by=user, statut='confirmee'
            )
            clients_filter = Client.objects.all()
            entrepots_filter = Entrepot.objects.filter(actif=True)

        total_ventes = ventes_filter.count()
        chiffre_affaires = ventes_filter.aggregate(Sum('montant_total'))[
            'montant_total__sum'] or 0
        total_clients = clients_filter.count()
        total_produits = Produit.objects.count()
        total_entrepots = entrepots_filter.count()

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

        ventes_mois = ventes_filter.filter(created_at__gte=month_start).aggregate(
            Sum('montant_total'))['montant_total__sum'] or 0

        ventes_semaine = ventes_filter.filter(created_at__gte=week_start).aggregate(
            Sum('montant_total'))['montant_total__sum'] or 0

        produits_low_stock = []
        stocks_faibles = StockEntrepot.objects.filter(
            quantite__gt=models.F('quantite_reservee'),
            quantite__lte=models.F('quantite_reservee') +
            models.F('stock_alerte')
        ).select_related('produit', 'entrepot')

        for stock in stocks_faibles[:10]:
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

        dernieres_ventes = ventes_filter.order_by('-created_at')[:5]
        ventes_serializer = VenteSerializer(dernieres_ventes, many=True)

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

        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(user__email__icontains=search) |
                Q(modele__icontains=search) |
                Q(action__icontains=search) |
                Q(details__icontains=search)
            )

        action = self.request.query_params.get('action')
        if action:
            queryset = queryset.filter(action=action)

        modele = self.request.query_params.get('modele')
        if modele:
            queryset = queryset.filter(modele=modele)

        date_debut = self.request.query_params.get('date_debut')
        date_fin = self.request.query_params.get('date_fin')

        if date_debut:
            queryset = queryset.filter(created_at__date__gte=date_debut)
        if date_fin:
            queryset = queryset.filter(created_at__date__lte=date_fin)

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
        user = request.user
        date_debut = request.query_params.get('date_debut')
        date_fin = request.query_params.get('date_fin')
        categorie_id = request.query_params.get('categorie')
        vendeur_id = request.query_params.get('vendeur')
        entrepot_id = request.query_params.get('entrepot')

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

        if vendeur_id and user.role == 'admin':
            queryset = queryset.filter(created_by_id=vendeur_id)

        if entrepot_id:
            queryset = queryset.filter(
                lignes_vente__entrepot_id=entrepot_id
            ).distinct()

        if categorie_id:
            queryset = queryset.filter(
                lignes_vente__produit__categorie_id=categorie_id
            ).distinct()

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

        ventes_detaillees = VenteSerializer(
            queryset.order_by('-created_at')[:50],
            many=True
        ).data

        return Response({
            'stats': stats,
            'ventes_detaillees': ventes_detaillees
        })

    @action(detail=False, methods=['get'])
    def stocks(self, request):
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


class StatistiquesViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminOrVendeur]

    @action(detail=False, methods=['get'])
    def evolution_ventes(self, request):
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


class StockOperationsViewSet(viewsets.ViewSet):
    permission_classes = [IsAdmin]

    @action(detail=False, methods=['post'])
    def ajuster_stock(self, request):
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
                else:
                    stock.quantite = max(0, stock.quantite - data['quantite'])

                stock.save()

                MouvementStock.objects.create(
                    produit=data['produit'],
                    type_mouvement='ajustement',
                    quantite=data['quantite'],
                    prix_unitaire=data['produit'].prix_achat,
                    motif=data['motif'],
                    entrepot=data['entrepot'],
                    created_by=request.user
                )

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
    def liberer_stock_reserve(self, request):
        """Libérer tout le stock réservé (pour debug)"""
        if not request.user.is_superuser:
            return Response({'error': 'Permission refusée'}, status=403)

        try:
            with transaction.atomic():
                stocks = StockEntrepot.objects.all()
                total_liberes = 0

                for stock in stocks:
                    if stock.quantite_reservee > 0:
                        ancienne_reserve = stock.quantite_reservee
                        stock.quantite_reservee = 0
                        stock.save()
                        total_liberes += ancienne_reserve

                return Response({
                    'message': f'{total_liberes} unités de stock réservé libérées',
                    'total_liberes': total_liberes
                })
        except Exception as e:
            return Response({'error': str(e)}, status=400)
