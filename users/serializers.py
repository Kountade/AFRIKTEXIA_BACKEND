from django.db.models import Sum
from rest_framework import serializers
from .models import *
from django.contrib.auth import get_user_model
from datetime import datetime
from django.db import transaction
from django.utils import timezone

User = get_user_model()


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret.pop('password', None)
        return ret


class RegisterSerializer(serializers.ModelSerializer):
    role = serializers.ChoiceField(
        choices=CustomUser.ROLE_CHOICES, required=False)

    class Meta:
        model = User
        fields = ('id', 'email', 'password', 'role', 'telephone', 'adresse')
        extra_kwargs = {
            'password': {'write_only': True},
            'role': {'required': False}
        }

    def create(self, validated_data):
        if 'role' not in validated_data:
            validated_data['role'] = 'vendeur'
        user = User.objects.create_user(**validated_data)
        return user


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'email', 'role', 'birthday',
                  'username', 'telephone', 'adresse')
        read_only_fields = ('id', 'email', 'role')


class UserDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'email', 'role', 'birthday', 'username',
                  'telephone', 'adresse', 'is_staff', 'is_superuser')
        read_only_fields = ('id', 'is_staff', 'is_superuser')


class CategorieSerializer(serializers.ModelSerializer):
    created_by_email = serializers.CharField(
        source='created_by.email', read_only=True)
    nombre_produits = serializers.SerializerMethodField()

    class Meta:
        model = Categorie
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at')

    def get_nombre_produits(self, obj):
        return obj.produit_set.count()


class FournisseurSerializer(serializers.ModelSerializer):
    created_by_email = serializers.CharField(
        source='created_by.email', read_only=True)

    class Meta:
        model = Fournisseur
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at')


class ProduitSerializer(serializers.ModelSerializer):
    stock_actuel = serializers.SerializerMethodField()
    stock_total = serializers.SerializerMethodField()
    stock_reserve_total = serializers.SerializerMethodField()
    stock_disponible_total = serializers.SerializerMethodField()
    en_rupture = serializers.SerializerMethodField()
    stock_faible = serializers.SerializerMethodField()
    categorie_nom = serializers.CharField(
        source='categorie.nom', read_only=True)
    fournisseur_nom = serializers.CharField(
        source='fournisseur.nom', read_only=True)
    created_by_email = serializers.CharField(
        source='created_by.email', read_only=True)
    stocks_entrepots = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = Produit
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at', 'thumbnail')

    def get_stock_actuel(self, obj):
        return obj.stock_actuel()

    def get_stock_total(self, obj):
        """Stock total dans tous les entrepôts"""
        total = StockEntrepot.objects.filter(produit=obj).aggregate(
            total=Sum('quantite')
        )['total'] or 0
        return total

    def get_stock_reserve_total(self, obj):
        """Stock réservé total dans tous les entrepôts"""
        total = StockEntrepot.objects.filter(produit=obj).aggregate(
            total=Sum('quantite_reservee')
        )['total'] or 0
        return total

    def get_stock_disponible_total(self, obj):
        """Stock disponible total dans tous les entrepôts"""
        total_stock = self.get_stock_total(obj)
        total_reserve = self.get_stock_reserve_total(obj)
        return total_stock - total_reserve

    def get_en_rupture(self, obj):
        return obj.en_rupture

    def get_stock_faible(self, obj):
        return obj.stock_faible

    def get_stocks_entrepots(self, obj):
        stocks = StockEntrepot.objects.filter(produit=obj)
        return StockEntrepotSerializer(stocks, many=True, read_only=True).data

    def get_image_url(self, obj):
        if obj.image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.image.url)
            return obj.image.url
        return None

    def get_thumbnail_url(self, obj):
        if obj.thumbnail:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.thumbnail.url)
            return obj.thumbnail.url
        elif obj.image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.image.url)
            return obj.image.url
        return None


class ClientSerializer(serializers.ModelSerializer):
    created_by_email = serializers.CharField(
        source='created_by.email', read_only=True)

    class Meta:
        model = Client
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at')


class MouvementStockSerializer(serializers.ModelSerializer):
    produit_nom = serializers.CharField(source='produit.nom', read_only=True)
    entrepot_nom = serializers.CharField(source='entrepot.nom', read_only=True)
    created_by_email = serializers.CharField(
        source='created_by.email', read_only=True)

    class Meta:
        model = MouvementStock
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at')


class EntrepotSerializer(serializers.ModelSerializer):
    responsable_email = serializers.CharField(
        source='responsable.email', read_only=True)
    created_by_email = serializers.CharField(
        source='created_by.email', read_only=True)
    stock_total_valeur = serializers.ReadOnlyField()
    produits_count = serializers.ReadOnlyField()

    class Meta:
        model = Entrepot
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at')


class StockEntrepotSerializer(serializers.ModelSerializer):
    entrepot_nom = serializers.CharField(source='entrepot.nom', read_only=True)
    produit_nom = serializers.CharField(source='produit.nom', read_only=True)
    produit_code = serializers.CharField(source='produit.code', read_only=True)
    quantite_disponible = serializers.ReadOnlyField()
    en_rupture = serializers.ReadOnlyField()
    stock_faible = serializers.ReadOnlyField()
    stock_total = serializers.IntegerField(source='quantite', read_only=True)
    stock_reserve = serializers.IntegerField(
        source='quantite_reservee', read_only=True)

    class Meta:
        model = StockEntrepot
        fields = '__all__'


class StockDetailSerializer(serializers.ModelSerializer):
    entrepot_nom = serializers.CharField(source='entrepot.nom', read_only=True)
    produit_nom = serializers.CharField(source='produit.nom', read_only=True)
    produit_code = serializers.CharField(source='produit.code', read_only=True)
    quantite_disponible = serializers.SerializerMethodField()

    class Meta:
        model = StockEntrepot
        fields = ['id', 'entrepot', 'entrepot_nom', 'produit', 'produit_nom', 'produit_code',
                  'quantite', 'quantite_reservee', 'quantite_disponible', 'stock_alerte',
                  'emplacement', 'created_at', 'updated_at']

    def get_quantite_disponible(self, obj):
        return obj.quantite_disponible


class LigneDeVenteCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LigneDeVente
        fields = ('produit', 'entrepot', 'quantite', 'prix_unitaire')
        extra_kwargs = {
            'produit': {'required': True},
            'entrepot': {'required': True},
            'quantite': {'required': True},
        }


class LigneDeVenteSerializer(serializers.ModelSerializer):
    produit_nom = serializers.CharField(source='produit.nom', read_only=True)
    produit_code = serializers.CharField(source='produit.code', read_only=True)
    entrepot_nom = serializers.CharField(source='entrepot.nom', read_only=True)
    sous_total = serializers.ReadOnlyField()
    est_prix_gros = serializers.BooleanField(read_only=True)

    class Meta:
        model = LigneDeVente
        fields = '__all__'


class VenteSerializer(serializers.ModelSerializer):
    client_nom = serializers.CharField(source='client.nom', read_only=True)
    created_by_email = serializers.CharField(
        source='created_by.email', read_only=True)
    lignes_vente = LigneDeVenteSerializer(many=True, read_only=True)
    montant_total = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True)
    entrepots_noms = serializers.SerializerMethodField()

    class Meta:
        model = Vente
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at', 'numero_vente')

    def get_entrepots_noms(self, obj):
        return [entrepot.nom for entrepot in obj.entrepots.all()]


class VenteCreateSerializer(serializers.ModelSerializer):
    lignes_vente = LigneDeVenteCreateSerializer(many=True, write_only=True)

    class Meta:
        model = Vente
        fields = ('client', 'type_vente', 'remise', 'lignes_vente', 'mode_paiement',
                  'montant_paye', 'date_echeance', 'notes')
        read_only_fields = ('created_by', 'created_at', 'numero_vente')
        extra_kwargs = {
            'client': {'required': False, 'allow_null': True},
            'remise': {'required': False, 'default': 0},
            'type_vente': {'required': False, 'default': 'detail'}
        }

    def validate_lignes_vente(self, value):
        if not value or len(value) == 0:
            raise serializers.ValidationError(
                "Au moins une ligne de vente est requise."
            )

        for ligne in value:
            produit = ligne.get('produit')
            entrepot = ligne.get('entrepot')
            quantite = ligne.get('quantite')

            if not produit or not entrepot or not quantite or quantite <= 0:
                raise serializers.ValidationError(
                    "Chaque ligne doit avoir un produit, un entrepôt et une quantité positive."
                )

            # Vérifier le stock disponible
            try:
                stock_entrepot = StockEntrepot.objects.get(
                    produit=produit,
                    entrepot=entrepot
                )
                disponible = stock_entrepot.quantite_disponible
                if quantite > disponible:
                    raise serializers.ValidationError(
                        f"Stock insuffisant pour {produit.nom} dans {entrepot.nom}. Disponible: {disponible}"
                    )
            except StockEntrepot.DoesNotExist:
                raise serializers.ValidationError(
                    f"Le produit {produit.nom} n'est pas disponible dans {entrepot.nom}"
                )

        return value

    @transaction.atomic
    def create(self, validated_data):
        """Création simplifiée SANS réservation de stock"""
        lignes_data = validated_data.pop('lignes_vente')

        # Récupérer l'utilisateur depuis le contexte
        user = self.context.get(
            'request').user if self.context.get('request') else None

        if not user or not user.is_authenticated:
            raise serializers.ValidationError({
                'non_field_errors': 'Utilisateur non authentiqué'
            })

        # Générer numéro de vente
        today = datetime.now().strftime('%Y%m%d')
        last_vente_today = Vente.objects.filter(
            numero_vente__startswith=f'V{today}'
        ).order_by('-numero_vente').first()

        if last_vente_today:
            try:
                last_number = int(last_vente_today.numero_vente[-4:])
                new_number = last_number + 1
            except (ValueError, IndexError):
                new_number = 1
        else:
            new_number = 1

        numero_vente = f'V{today}{new_number:04d}'

        # Créer la vente AVEC created_by
        vente = Vente.objects.create(
            numero_vente=numero_vente,
            client=validated_data.get('client'),
            type_vente=validated_data.get('type_vente', 'detail'),
            remise=validated_data.get('remise', 0),
            mode_paiement=validated_data.get('mode_paiement'),
            montant_paye=validated_data.get('montant_paye', 0),
            date_echeance=validated_data.get('date_echeance'),
            notes=validated_data.get('notes', ''),
            created_by=user
        )

        # Créer les lignes de vente SANS RÉSERVER LE STOCK ICI
        entrepots_utilises = set()
        montant_total = 0

        for ligne_data in lignes_data:
            produit = ligne_data.get('produit')
            entrepot = ligne_data.get('entrepot')
            quantite = ligne_data.get('quantite')
            type_vente = validated_data.get('type_vente', 'detail')

            # Déterminer le prix selon type de vente
            if type_vente == 'gros':
                prix_unitaire = produit.prix_vente_gros or produit.prix_vente or 0
                est_prix_gros = True
            else:
                prix_unitaire = produit.prix_vente_detail or produit.prix_vente or 0
                est_prix_gros = False

            # Créer la ligne
            ligne = LigneDeVente.objects.create(
                vente=vente,
                produit=produit,
                entrepot=entrepot,
                quantite=quantite,
                prix_unitaire=prix_unitaire,
                est_prix_gros=est_prix_gros
            )

            # Calculer le sous-total
            sous_total = quantite * prix_unitaire
            ligne.montant_total = sous_total
            ligne.save()

            montant_total += sous_total
            entrepots_utilises.add(entrepot)

            # ⚠️ NE PAS RÉSERVER LE STOCK ICI - Cela sera fait dans la vue

        # Ajouter les entrepôts à la vente
        vente.entrepots.set(entrepots_utilises)

        # Appliquer la remise
        remise = validated_data.get('remise', 0)
        if remise > 0 and remise <= 100:
            montant_remise = montant_total * remise / 100
            montant_total_apres_remise = montant_total - montant_remise
        else:
            montant_remise = 0
            montant_total_apres_remise = montant_total

        # Mettre à jour la vente
        vente.montant_total = montant_total_apres_remise
        vente.montant_avant_remise = montant_total
        vente.montant_remise = montant_remise
        vente.montant_restant = max(
            0, montant_total_apres_remise - vente.montant_paye)

        # Déterminer statut paiement
        if vente.montant_paye >= montant_total_apres_remise:
            vente.statut_paiement = 'paye'
        elif vente.montant_paye > 0:
            vente.statut_paiement = 'partiel'
        else:
            vente.statut_paiement = 'non_paye'

        vente.save()

        # Audit log
        try:
            AuditLog.objects.create(
                user=user,
                action='creation',
                modele='Vente',
                objet_id=vente.id,
                details={
                    'numero_vente': numero_vente,
                    'montant_total': str(vente.montant_total),
                    'type_vente': type_vente,
                    'nombre_lignes': len(lignes_data)
                }
            )
        except Exception as e:
            print(f"⚠️ Audit log error (non bloquant): {e}")

        return vente


class PaiementSerializer(serializers.ModelSerializer):
    created_by_email = serializers.CharField(
        source='created_by.email', read_only=True)
    mode_paiement_display = serializers.CharField(
        source='get_mode_paiement_display', read_only=True)

    class Meta:
        model = Paiement
        fields = '__all__'
        read_only_fields = ('created_by', 'date_paiement')


class FactureSerializer(serializers.ModelSerializer):
    vente_numero = serializers.CharField(
        source='vente.numero_vente', read_only=True)

    class Meta:
        model = Facture
        fields = '__all__'


class VenteDetailSerializer(serializers.ModelSerializer):
    client_nom = serializers.CharField(source='client.nom', read_only=True)
    created_by_email = serializers.CharField(
        source='created_by.email', read_only=True)
    lignes_vente = LigneDeVenteSerializer(many=True, read_only=True)
    paiements = PaiementSerializer(many=True, read_only=True)
    facture = FactureSerializer(read_only=True)
    pourcentage_paye = serializers.SerializerMethodField()
    jours_retard = serializers.SerializerMethodField()
    statut_paiement_display = serializers.CharField(
        source='get_statut_paiement_display', read_only=True)
    mode_paiement_display = serializers.CharField(
        source='get_mode_paiement_display', read_only=True)

    class Meta:
        model = Vente
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at', 'numero_vente')

    def get_pourcentage_paye(self, obj):
        return obj.pourcentage_paye()

    def get_jours_retard(self, obj):
        return obj.jours_retard()


class VenteUpdateSerializer(serializers.ModelSerializer):
    lignes_vente = LigneDeVenteCreateSerializer(
        many=True, write_only=True, required=False)

    class Meta:
        model = Vente
        fields = ('client', 'remise', 'lignes_vente', 'mode_paiement',
                  'montant_paye', 'date_echeance', 'notes', 'statut')
        read_only_fields = ('created_by', 'created_at', 'numero_vente')

    def validate(self, data):
        vente = self.instance

        if vente.statut not in ['brouillon']:
            raise serializers.ValidationError(
                "Seules les ventes en brouillon peuvent être modifiées"
            )

        lignes_data = data.get('lignes_vente')
        if lignes_data:
            if not lignes_data or len(lignes_data) == 0:
                raise serializers.ValidationError(
                    "Au moins une ligne de vente est requise."
                )

            for ligne in lignes_data:
                produit = ligne.get('produit')
                entrepot = ligne.get('entrepot')
                quantite = ligne.get('quantite')

                if not produit or not entrepot or not quantite or quantite <= 0:
                    raise serializers.ValidationError(
                        "Chaque ligne doit avoir un produit, un entrepôt et une quantité positive."
                    )

                if not ligne.get('prix_unitaire') or ligne['prix_unitaire'] <= 0:
                    raise serializers.ValidationError(
                        "Le prix unitaire doit être positif."
                    )

                try:
                    stock_entrepot = StockEntrepot.objects.get(
                        produit=produit,
                        entrepot=entrepot
                    )
                    if quantite > stock_entrepot.quantite_disponible:
                        raise serializers.ValidationError({
                            'lignes_vente': f'Stock insuffisant pour {produit.nom} dans {entrepot.nom}. Disponible: {stock_entrepot.quantite_disponible}'
                        })
                except StockEntrepot.DoesNotExist:
                    raise serializers.ValidationError({
                        'lignes_vente': f'Le produit {produit.nom} n\'est pas disponible dans {entrepot.nom}'
                    })

        return data

    @transaction.atomic
    def update(self, instance, validated_data):
        lignes_data = validated_data.pop('lignes_vente', None)

        # ⚠️ IMPORTANT : NE PAS libérer/réserver le stock ici !
        # La vue VenteViewSet.update() gère cela avec calcul intelligent des différences

        # Mettre à jour les autres champs
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        # Si des lignes sont fournies, simplement les mettre à jour
        # Le stock sera ajusté dans la vue
        if lignes_data:
            # Supprimer les anciennes lignes
            instance.lignes_vente.all().delete()

            # Créer les nouvelles lignes
            entrepots_utilises = set()
            for ligne_data in lignes_data:
                ligne = LigneDeVente.objects.create(
                    vente=instance, **ligne_data)
                entrepots_utilises.add(ligne.entrepot)

            # Mettre à jour les entrepôts
            instance.entrepots.set(entrepots_utilises)

        # Recalculer le montant total
        instance.montant_total = instance.calculer_total()
        instance.save()

        return instance


class EnregistrerPaiementSerializer(serializers.Serializer):
    montant = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=0.01
    )
    mode_paiement = serializers.ChoiceField(choices=Vente.MODE_PAIEMENT)
    reference = serializers.CharField(
        required=False, allow_blank=True, max_length=100
    )
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        vente = self.context['vente']
        montant = data['montant']

        if montant <= 0:
            raise serializers.ValidationError({
                'montant': 'Le montant doit être supérieur à 0'
            })

        montant_restant = vente.montant_restant
        if montant > montant_restant:
            raise serializers.ValidationError({
                'montant': f"Le montant ({montant}) dépasse le montant restant ({montant_restant})"
            })

        if vente.statut != 'confirmee':
            raise serializers.ValidationError({
                'non_field_errors': 'Seules les ventes confirmées peuvent recevoir des paiements'
            })

        if vente.statut_paiement == 'paye':
            raise serializers.ValidationError({
                'non_field_errors': 'Cette vente est déjà entièrement payée'
            })

        return data


class LigneTransfertSerializer(serializers.ModelSerializer):
    produit_nom = serializers.CharField(source='produit.nom', read_only=True)
    produit_code = serializers.CharField(source='produit.code', read_only=True)

    class Meta:
        model = LigneTransfert
        fields = '__all__'
        read_only_fields = ('id',)


class LigneTransfertCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LigneTransfert
        fields = ('produit', 'quantite')


class TransfertEntrepotSerializer(serializers.ModelSerializer):
    entrepot_source_nom = serializers.CharField(
        source='entrepot_source.nom', read_only=True)
    entrepot_destination_nom = serializers.CharField(
        source='entrepot_destination.nom', read_only=True)
    created_by_email = serializers.CharField(
        source='created_by.email', read_only=True)
    lignes_transfert = LigneTransfertSerializer(many=True, read_only=True)
    total_quantite = serializers.SerializerMethodField()

    class Meta:
        model = TransfertEntrepot
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at', 'reference')

    def get_total_quantite(self, obj):
        return sum(ligne.quantite for ligne in obj.lignes_transfert.all())


class TransfertEntrepotCreateSerializer(serializers.ModelSerializer):
    lignes_transfert = LigneTransfertCreateSerializer(
        many=True, write_only=True)

    class Meta:
        model = TransfertEntrepot
        fields = ('entrepot_source', 'entrepot_destination',
                  'motif', 'lignes_transfert')

    def validate(self, data):
        if data['entrepot_source'] == data['entrepot_destination']:
            raise serializers.ValidationError({
                'entrepot_destination': "L'entrepôt source et destination doivent être différents"
            })

        if not data.get('lignes_transfert') or len(data['lignes_transfert']) == 0:
            raise serializers.ValidationError({
                'lignes_transfert': "Ajoutez au moins un produit au transfert"
            })

        for ligne in data['lignes_transfert']:
            produit = ligne['produit']
            quantite = ligne['quantite']

            if quantite <= 0:
                raise serializers.ValidationError({
                    'lignes_transfert': f"La quantité pour {produit.nom} doit être positive"
                })

            try:
                stock_source = StockEntrepot.objects.get(
                    produit=produit,
                    entrepot=data['entrepot_source']
                )

                if quantite > stock_source.quantite_disponible:
                    raise serializers.ValidationError({
                        'lignes_transfert': f"Stock insuffisant pour {produit.nom}. Disponible: {stock_source.quantite_disponible}"
                    })

            except StockEntrepot.DoesNotExist:
                raise serializers.ValidationError({
                    'lignes_transfert': f"Le produit {produit.nom} n'est pas disponible dans {data['entrepot_source'].nom}"
                })

        return data

    @transaction.atomic
    def create(self, validated_data):
        lignes_data = validated_data.pop('lignes_transfert')

        today = datetime.now().strftime('%Y%m%d')
        last_transfert_today = TransfertEntrepot.objects.filter(
            created_at__date=timezone.now().date()
        ).count()
        reference = f"TRF{today}{last_transfert_today + 1:04d}"

        transfert = TransfertEntrepot.objects.create(
            reference=reference,
            created_by=self.context['request'].user,
            **validated_data
        )

        for ligne_data in lignes_data:
            LigneTransfert.objects.create(transfert=transfert, **ligne_data)

        return transfert


class StockDisponibleSerializer(serializers.Serializer):
    produit_id = serializers.IntegerField()

    def validate_produit_id(self, value):
        try:
            Produit.objects.get(id=value)
        except Produit.DoesNotExist:
            raise serializers.ValidationError("Produit non trouvé")
        return value


class DashboardStatsSerializer(serializers.Serializer):
    total_ventes = serializers.IntegerField()
    chiffre_affaires = serializers.DecimalField(
        max_digits=12, decimal_places=2)
    total_clients = serializers.IntegerField()
    total_produits = serializers.IntegerField()
    total_entrepots = serializers.IntegerField()
    valeur_stock_total = serializers.DecimalField(
        max_digits=12, decimal_places=2)


class RapportVentesSerializer(serializers.Serializer):
    date_debut = serializers.DateField(required=False)
    date_fin = serializers.DateField(required=False)
    categorie_id = serializers.IntegerField(required=False)
    vendeur_id = serializers.IntegerField(required=False)


class AuditLogSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = AuditLog
        fields = '__all__'


class StockVerificationSerializer(serializers.Serializer):
    produit_id = serializers.IntegerField(required=True)
    entrepot_id = serializers.IntegerField(required=True)
    quantite = serializers.IntegerField(required=True, min_value=1)
