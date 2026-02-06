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
        """Création d'une vente avec réservation unique du stock"""
        try:
            with transaction.atomic():
                # Valider et créer la vente via serializer
                serializer = self.get_serializer(data=request.data)
                serializer.is_valid(raise_exception=True)

                # Sauvegarder la vente
                self.perform_create(serializer)
                vente = serializer.instance

                # ✅ UN SEUL ENDROIT POUR RÉSERVER LE STOCK : ICI !
                stocks_reserves = []

                for ligne in vente.lignes_vente.all():
                    try:
                        stock_entrepot = StockEntrepot.objects.get(
                            produit=ligne.produit,
                            entrepot=ligne.entrepot
                        )

                        # Vérifier que la quantité est disponible
                        stock_disponible = stock_entrepot.quantite - stock_entrepot.quantite_reservee

                        if ligne.quantite > stock_disponible:
                            raise serializers.ValidationError({
                                'lignes_vente': f'Stock insuffisant pour {ligne.produit.nom} dans {ligne.entrepot.nom}. Disponible: {stock_disponible}'
                            })

                        # Réserver le stock (SEULEMENT ICI)
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

                # Log d'audit pour la création avec réservation
                AuditLog.objects.create(
                    user=request.user,
                    action='creation',
                    modele='Vente',
                    objet_id=vente.id,
                    details={
                        'numero_vente': vente.numero_vente,
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
        Mise à jour d'une vente avec ajustement intelligent des réservations
        Évite le double comptage en calculant la différence
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

                # Sauvegarder les anciennes lignes avec leurs quantités
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
                self.perform_update(serializer)

                # Traiter les ajustements de stock
                ajustements = []
                stocks_reserves = []
                stocks_liberes = []

                # Analyser les nouvelles lignes après la mise à jour
                for nouvelle_ligne in instance.lignes_vente.all():
                    key = (nouvelle_ligne.produit_id,
                           nouvelle_ligne.entrepot_id)

                    try:
                        stock_entrepot = StockEntrepot.objects.get(
                            produit=nouvelle_ligne.produit,
                            entrepot=nouvelle_ligne.entrepot
                        )

                        if key in anciennes_lignes_dict:
                            # Ligne existante modifiée
                            ancienne_quantite = anciennes_lignes_dict[key]['quantite']
                            nouvelle_quantite = nouvelle_ligne.quantite

                            # Calculer la différence
                            difference = nouvelle_quantite - ancienne_quantite

                            if difference > 0:
                                # Augmentation de la quantité - vérifier disponibilité
                                stock_disponible = stock_entrepot.quantite - stock_entrepot.quantite_reservee

                                if difference > stock_disponible:
                                    raise serializers.ValidationError({
                                        'lignes_vente': f'Stock insuffisant pour augmenter {nouvelle_ligne.produit.nom}. Différence: {difference}, Disponible: {stock_disponible}'
                                    })

                                # Réserver la différence
                                ancienne_reserve = stock_entrepot.quantite_reservee
                                stock_entrepot.quantite_reservee += difference
                                stock_entrepot.save()

                                ajustements.append({
                                    'produit': nouvelle_ligne.produit.nom,
                                    'entrepot': nouvelle_ligne.entrepot.nom,
                                    'type': 'augmentation',
                                    'ancienne_quantite': ancienne_quantite,
                                    'nouvelle_quantite': nouvelle_quantite,
                                    'difference': difference,
                                    'ancienne_reserve': ancienne_reserve,
                                    'nouvelle_reserve': stock_entrepot.quantite_reservee
                                })

                            elif difference < 0:
                                # Diminution de la quantité - libérer la différence
                                ancienne_reserve = stock_entrepot.quantite_reservee
                                stock_entrepot.quantite_reservee = max(
                                    0, stock_entrepot.quantite_reservee + difference  # difference est négative
                                )
                                stock_entrepot.save()

                                ajustements.append({
                                    'produit': nouvelle_ligne.produit.nom,
                                    'entrepot': nouvelle_ligne.entrepot.nom,
                                    'type': 'diminution',
                                    'ancienne_quantite': ancienne_quantite,
                                    'nouvelle_quantite': nouvelle_quantite,
                                    'difference': difference,
                                    'ancienne_reserve': ancienne_reserve,
                                    'nouvelle_reserve': stock_entrepot.quantite_reservee
                                })
                                stocks_liberes.append({
                                    'produit': nouvelle_ligne.produit.nom,
                                    'entrepot': nouvelle_ligne.entrepot.nom,
                                    'quantite_liberee': abs(difference)
                                })

                        else:
                            # Nouvelle ligne - réserver la quantité
                            if not nouvelle_ligne.stock_preleve:
                                stock_disponible = stock_entrepot.quantite - stock_entrepot.quantite_reservee

                                if nouvelle_ligne.quantite > stock_disponible:
                                    raise serializers.ValidationError({
                                        'lignes_vente': f'Stock insuffisant pour {nouvelle_ligne.produit.nom}. Demandé: {nouvelle_ligne.quantite}, Disponible: {stock_disponible}'
                                    })

                                ancienne_reserve = stock_entrepot.quantite_reservee
                                stock_entrepot.quantite_reservee += nouvelle_ligne.quantite
                                stock_entrepot.save()

                                stocks_reserves.append({
                                    'produit': nouvelle_ligne.produit.nom,
                                    'entrepot': nouvelle_ligne.entrepot.nom,
                                    'quantite': nouvelle_ligne.quantite,
                                    'ancienne_reserve': ancienne_reserve,
                                    'nouvelle_reserve': stock_entrepot.quantite_reservee
                                })

                        # Supprimer de la liste des anciennes lignes
                        if key in anciennes_lignes_dict:
                            del anciennes_lignes_dict[key]

                    except StockEntrepot.DoesNotExist:
                        raise serializers.ValidationError({
                            'lignes_vente': f'Stock non trouvé pour {nouvelle_ligne.produit.nom} dans {nouvelle_ligne.entrepot.nom}'
                        })

                # Libérer le stock des lignes supprimées
                for key, ancienne_ligne in anciennes_lignes_dict.items():
                    # Ne libérer que si pas prélevé
                    if not ancienne_ligne['stock_preleve']:
                        try:
                            stock_entrepot = StockEntrepot.objects.get(
                                produit_id=key[0],
                                entrepot_id=key[1]
                            )

                            ancienne_reserve = stock_entrepot.quantite_reservee
                            stock_entrepot.quantite_reservee = max(
                                0, stock_entrepot.quantite_reservee -
                                ancienne_ligne['quantite']
                            )
                            stock_entrepot.save()

                            stocks_liberes.append({
                                'produit_id': key[0],
                                'entrepot_id': key[1],
                                'quantite_liberee': ancienne_ligne['quantite'],
                                'ancienne_reserve': ancienne_reserve,
                                'nouvelle_reserve': stock_entrepot.quantite_reservee
                            })

                        except StockEntrepot.DoesNotExist:
                            continue

                # Log d'audit pour la modification
                AuditLog.objects.create(
                    user=request.user,
                    action='modification',
                    modele='Vente',
                    objet_id=instance.id,
                    details={
                        'numero_vente': instance.numero_vente,
                        'ajustements': ajustements,
                        'stocks_reserves': stocks_reserves,
                        'stocks_liberes': stocks_liberes
                    }
                )

                return Response({
                    'message': 'Vente mise à jour avec succès',
                    'vente': serializer.data,
                    'ajustements': ajustements,
                    'stocks_reserves': stocks_reserves,
                    'stocks_liberes': stocks_liberes
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

                vente.confirmer_vente()

                vente.refresh_from_db()

                return Response({
                    "message": "Vente confirmée avec succès",
                    "vente": VenteDetailSerializer(vente).data
                }, status=status.HTTP_200_OK)

        except Vente.DoesNotExist:
            return Response({"error": "Vente non trouvée"}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": f"Erreur interne: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def annuler(self, request, pk=None):
        try:
            with transaction.atomic():
                vente = self.get_object()

                if vente.statut != 'brouillon':
                    return Response(
                        {"error": "Seules les ventes en brouillon peuvent être annulées"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                stocks_libérés = []
                for ligne in vente.lignes_vente.all():
                    try:
                        stock_entrepot = StockEntrepot.objects.get(
                            produit=ligne.produit,
                            entrepot=ligne.entrepot
                        )
                        stock_entrepot.liberer_stock(ligne.quantite)
                        stocks_libérés.append({
                            'produit': ligne.produit.nom,
                            'entrepot': ligne.entrepot.nom,
                            'quantite': ligne.quantite
                        })
                    except StockEntrepot.DoesNotExist:
                        continue

                vente.statut = 'annulee'
                vente.save()

                vente.refresh_from_db()

                return Response({
                    "message": "Vente annulée avec succès",
                    "stocks_libérés": stocks_libérés,
                    "vente": VenteDetailSerializer(vente).data
                }, status=status.HTTP_200_OK)

        except Vente.DoesNotExist:
            return Response({"error": "Vente non trouvée"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def enregistrer_paiement(self, request, pk=None):
        try:
            with transaction.atomic():
                vente = self.get_object()

                if vente.statut != 'confirmee':
                    return Response(
                        {"error": "Seules les ventes confirmées peuvent recevoir des paiements"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

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

                    paiement = Paiement.objects.create(
                        vente=vente,
                        montant=data['montant'],
                        mode_paiement=data['mode_paiement'],
                        reference=data.get('reference', ''),
                        notes=data.get('notes', ''),
                        created_by=request.user
                    )

                    vente.montant_paye += data['montant']

                    if not vente.mode_paiement:
                        vente.mode_paiement = data['mode_paiement']

                    vente.update_statut_paiement()

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

    def destroy(self, request, pk=None):
        """Surcharger la suppression pour libérer le stock réservé"""
        try:
            with transaction.atomic():
                vente = self.get_object()

                # Libérer le stock réservé si la vente est en brouillon
                if vente.statut == 'brouillon':
                    stocks_libérés = []
                    for ligne in vente.lignes_vente.all():
                        try:
                            stock_entrepot = StockEntrepot.objects.get(
                                entrepot=ligne.entrepot,
                                produit=ligne.produit
                            )

                            ancienne_reserve = stock_entrepot.quantite_reservee
                            nouvelle_reserve = max(
                                0, ancienne_reserve - ligne.quantite)
                            stock_entrepot.quantite_reservee = nouvelle_reserve
                            stock_entrepot.save()

                            stocks_libérés.append({
                                'produit': ligne.produit.nom,
                                'entrepot': ligne.entrepot.nom,
                                'quantite': ligne.quantite,
                                'ancienne_reserve': ancienne_reserve,
                                'nouvelle_reserve': nouvelle_reserve
                            })

                        except StockEntrepot.DoesNotExist:
                            continue

                # Supprimer la vente
                vente.delete()

                return Response({
                    'message': 'Vente supprimée avec succès',
                    'stocks_libérés': stocks_libérés
                }, status=status.HTTP_200_OK)

        except Vente.DoesNotExist:
            return Response({'error': 'Vente non trouvée'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def statistiques(self, request):
        user = request.user
        queryset = self.get_queryset()

        total_ventes = queryset.count()
        ventes_confirmees = queryset.filter(statut='confirmee').count()
        ventes_brouillon = queryset.filter(statut='brouillon').count()
        ventes_annulees = queryset.filter(statut='annulee').count()

        chiffre_affaires = queryset.filter(statut='confirmee').aggregate(
            total=Sum('montant_total')
        )['total'] or 0

        montant_a_recouvrer = queryset.filter(
            statut='confirmee',
            statut_paiement__in=['non_paye', 'partiel']
        ).aggregate(
            total=Sum('montant_restant')
        )['total'] or 0

        ventes_en_retard = queryset.filter(
            statut='confirmee',
            date_echeance__lt=timezone.now().date(),
            statut_paiement__in=['non_paye', 'partiel']
        ).count()

        return Response({
            'total_ventes': total_ventes,
            'ventes_confirmees': ventes_confirmees,
            'ventes_brouillon': ventes_brouillon,
            'ventes_annulees': ventes_annulees,
            'chiffre_affaires': chiffre_affaires,
            'montant_a_recouvrer': montant_a_recouvrer,
            'ventes_en_retard': ventes_en_retard,
            'taux_confirmation': (ventes_confirmees / total_ventes * 100) if total_ventes > 0 else 0,
            'taux_paiement': ((chiffre_affaires - montant_a_recouvrer) / chiffre_affaires * 100)
            if chiffre_affaires > 0 else 0
        })

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

    @action(detail=True, methods=['get'])
    def paiements(self, request, pk=None):
        """Liste des paiements d'une vente"""
        try:
            vente = self.get_object()
            paiements = vente.paiements.all().order_by('-date_paiement')

            serializer = PaiementSerializer(paiements, many=True)
            return Response({
                'vente': VenteDetailSerializer(vente).data,
                'paiements': serializer.data,
                'total_paye': vente.montant_paye,
                'montant_restant': vente.montant_restant
            })

        except Vente.DoesNotExist:
            return Response({"error": "Vente non trouvée"}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['get'])
    def ventes_du_jour(self, request):
        """Liste des ventes créées aujourd'hui"""
        aujourd_hui = timezone.now().date()
        queryset = self.get_queryset().filter(
            created_at__date=aujourd_hui
        ).order_by('-created_at')

        total_ventes = queryset.count()
        total_montant = queryset.aggregate(
            total=Sum('montant_total'))['total'] or 0

        serializer = VenteSerializer(queryset, many=True)

        return Response({
            'date': aujourd_hui.strftime('%d/%m/%Y'),
            'total_ventes': total_ventes,
            'total_montant': float(total_montant),
            'ventes': serializer.data
        })

    @action(detail=False, methods=['get'])
    def ventes_du_mois(self, request):
        """Statistiques des ventes du mois en cours"""
        debut_mois = timezone.now().replace(day=1).date()
        aujourd_hui = timezone.now().date()

        queryset = self.get_queryset().filter(
            created_at__date__gte=debut_mois,
            created_at__date__lte=aujourd_hui
        )

        total_ventes = queryset.count()
        ventes_confirmees = queryset.filter(statut='confirmee').count()
        total_montant = queryset.filter(statut='confirmee').aggregate(
            total=Sum('montant_total')
        )['total'] or 0

        # Ventilation par jour
        jours = {}
        current_date = debut_mois
        while current_date <= aujourd_hui:
            jours[current_date.strftime('%Y-%m-%d')] = {
                'date': current_date.strftime('%d/%m'),
                'ventes': 0,
                'montant': 0
            }
            current_date += timedelta(days=1)

        ventes_par_jour = queryset.filter(statut='confirmee').values(
            'created_at__date'
        ).annotate(
            count=Count('id'),
            total=Sum('montant_total')
        ).order_by('created_at__date')

        for jour in ventes_par_jour:
            date_str = jour['created_at__date'].strftime('%Y-%m-%d')
            if date_str in jours:
                jours[date_str]['ventes'] = jour['count']
                jours[date_str]['montant'] = float(jour['total'] or 0)

        return Response({
            'periode': {
                'debut': debut_mois.strftime('%d/%m/%Y'),
                'fin': aujourd_hui.strftime('%d/%m/%Y')
            },
            'statistiques': {
                'total_ventes': total_ventes,
                'ventes_confirmees': ventes_confirmees,
                'chiffre_affaires': float(total_montant)
            },
            'evolution': list(jours.values())
        })

    @action(detail=False, methods=['get'])
    def export_csv(self, request):
        """Exporter les ventes en CSV"""
        queryset = self.get_queryset()

        # Créer la réponse HTTP avec le type CSV
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="ventes.csv"'

        writer = csv.writer(response)

        # Écrire l'en-tête
        writer.writerow([
            'Numéro', 'Date', 'Client', 'Type', 'Statut',
            'Montant Total', 'Montant Payé', 'Montant Restant',
            'Statut Paiement', 'Mode Paiement', 'Vendeur'
        ])

        # Écrire les données
        for vente in queryset:
            writer.writerow([
                vente.numero_vente,
                vente.created_at.strftime('%d/%m/%Y %H:%M'),
                vente.client.nom if vente.client else '',
                vente.get_type_vente_display(),
                vente.get_statut_display(),
                str(vente.montant_total),
                str(vente.montant_paye),
                str(vente.montant_restant),
                vente.get_statut_paiement_display(),
                vente.get_mode_paiement_display() if vente.mode_paiement else '',
                vente.created_by.email if vente.created_by else ''
            ])

        return response


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
