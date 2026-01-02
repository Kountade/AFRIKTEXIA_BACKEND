from .models import Produit, StockEntrepot
import os
from .models import Produit
from .models import TransfertEntrepot, LigneTransfert, StockEntrepot
from django.utils import timezone
from rest_framework import serializers
from .models import *
from django.contrib.auth import get_user_model
from datetime import datetime
from django.db import transaction

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
        # Si le rôle n'est pas fourni, défaut à 'vendeur'
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


# Dans serializers.py, modifiez ProduitSerializer


class ProduitSerializer(serializers.ModelSerializer):
    stock_actuel = serializers.SerializerMethodField()
    en_rupture = serializers.SerializerMethodField()
    stock_faible = serializers.SerializerMethodField()
    categorie_nom = serializers.CharField(
        source='categorie.nom', read_only=True)
    fournisseur_nom = serializers.CharField(
        source='fournisseur.nom', read_only=True)
    created_by_email = serializers.CharField(
        source='created_by.email', read_only=True)
    stocks_entrepots = serializers.SerializerMethodField()

    # Champs pour les images
    image_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = Produit
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at', 'thumbnail')

    def get_stock_actuel(self, obj):
        return obj.stock_actuel()

    def get_en_rupture(self, obj):
        return obj.en_rupture

    def get_stock_faible(self, obj):
        return obj.stock_faible

    def get_stocks_entrepots(self, obj):
        """Retourne les stocks du produit par entrepôt"""
        stocks = StockEntrepot.objects.filter(produit=obj)
        from .serializers import StockEntrepotSerializer
        return StockEntrepotSerializer(stocks, many=True, read_only=True).data

    def get_image_url(self, obj):
        """Retourne l'URL complète de l'image"""
        if obj.image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.image.url)
            return obj.image.url
        return None

    def get_thumbnail_url(self, obj):
        """Retourne l'URL complète de la miniature"""
        if obj.thumbnail:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.thumbnail.url)
            return obj.thumbnail.url
        elif obj.image:
            # Si pas de miniature mais une image existe
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.image.url)
            return obj.image.url
        return None

    def create(self, validated_data):
        """Création avec gestion d'image"""
        try:
            image = validated_data.get('image')

            # Créer le produit
            produit = Produit.objects.create(**validated_data)

            # Si une image existe, la sauvegarder
            if image:
                self._process_image(produit, image)

            return produit
        except Exception as e:
            raise serializers.ValidationError(
                f"Erreur lors de la création: {str(e)}")

    def update(self, instance, validated_data):
        """Mise à jour avec gestion d'image"""
        try:
            # Extraire l'image si elle existe
            image = validated_data.pop('image', None)

            # Mettre à jour les autres champs
            for attr, value in validated_data.items():
                setattr(instance, attr, value)

            # Sauvegarder
            instance.save()

            # Si nouvelle image, la traiter
            if image is not None:
                self._process_image(instance, image)

            return instance
        except Exception as e:
            raise serializers.ValidationError(
                f"Erreur lors de la mise à jour: {str(e)}")

    def _process_image(self, produit, image=None):
        """Traite l'image : redimensionne et génère une miniature"""
        try:
            if image is None:
                image = produit.image

            if image:
                # Sauvegarder l'image
                produit.image.save(
                    image.name,
                    image,
                    save=True
                )

                # Générer une miniature (simplifié)
                from PIL import Image as PILImage
                import io
                from django.core.files.base import ContentFile

                # Ouvrir l'image
                img = PILImage.open(produit.image.path)

                # Créer une miniature
                img.thumbnail((150, 150))

                # Sauvegarder la miniature
                thumb_io = io.BytesIO()
                img.save(thumb_io, format='JPEG')

                # Sauvegarder dans le modèle
                produit.thumbnail.save(
                    f"thumb_{os.path.basename(produit.image.name)}",
                    ContentFile(thumb_io.getvalue()),
                    save=True
                )

        except Exception as e:
            print(f"Erreur lors du traitement de l'image: {e}")
            # Ne pas échouer si le traitement d'image échoue
            pass


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


# Serializers pour les entrepôts
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

    class Meta:
        model = StockEntrepot
        fields = '__all__'


# Serializers pour les lignes de vente avec entrepôt
class LigneDeVenteCreateSerializer(serializers.ModelSerializer):
    """Serializer pour la création des lignes de vente avec entrepôt"""
    class Meta:
        model = LigneDeVente
        fields = ('produit', 'entrepot', 'quantite', 'prix_unitaire')
        extra_kwargs = {
            'produit': {'required': True},
            'entrepot': {'required': True},
            'quantite': {'required': True},
            'prix_unitaire': {'required': True}
        }


class LigneDeVenteSerializer(serializers.ModelSerializer):
    produit_nom = serializers.CharField(source='produit.nom', read_only=True)
    produit_code = serializers.CharField(source='produit.code', read_only=True)
    entrepot_nom = serializers.CharField(source='entrepot.nom', read_only=True)
    sous_total = serializers.ReadOnlyField()

    class Meta:
        model = LigneDeVente
        fields = '__all__'


# Serializers pour les ventes multi-entrepôts
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
        """Retourne la liste des noms des entrepôts utilisés"""
        return [entrepot.nom for entrepot in obj.entrepots.all()]


class VenteCreateSerializer(serializers.ModelSerializer):
    lignes_vente = LigneDeVenteCreateSerializer(many=True, write_only=True)

    class Meta:
        model = Vente
        fields = ('client', 'remise', 'lignes_vente')
        extra_kwargs = {
            'client': {'required': False, 'allow_null': True},
            'remise': {'required': False, 'default': 0}
        }

    def validate_lignes_vente(self, value):
        """Validation des lignes de vente avec vérification des stocks par entrepôt"""
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

            if not ligne.get('prix_unitaire') or ligne['prix_unitaire'] <= 0:
                raise serializers.ValidationError(
                    "Le prix unitaire doit être positif."
                )

            # Vérifier le stock dans l'entrepôt spécifié
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

        return value

    @transaction.atomic
    def create(self, validated_data):
        lignes_data = validated_data.pop('lignes_vente')

        # Générer numéro de vente unique
        today = datetime.now().strftime('%Y%m%d')
        last_vente_today = Vente.objects.filter(
            numero_vente__startswith=f'V{today}'
        ).count()
        numero_vente = f'V{today}{last_vente_today + 1:04d}'

        # Créer la vente
        vente = Vente.objects.create(
            numero_vente=numero_vente,
            created_by=self.context['request'].user,
            **validated_data
        )

        # Créer les lignes de vente et réserver le stock
        entrepots_utilises = set()
        for ligne_data in lignes_data:
            ligne = LigneDeVente.objects.create(vente=vente, **ligne_data)
            entrepots_utilises.add(ligne.entrepot)

            # Réserver le stock dans l'entrepôt
            stock_entrepot = StockEntrepot.objects.get(
                produit=ligne.produit,
                entrepot=ligne.entrepot
            )
            stock_entrepot.reserver_stock(ligne.quantite)

        # Ajouter les entrepôts utilisés à la vente
        vente.entrepots.set(entrepots_utilises)

        # Calculer et sauvegarder le montant total
        vente.montant_total = vente.calculer_total()
        vente.save()

        # Log d'audit
        AuditLog.objects.create(
            user=self.context['request'].user,
            action='vente',
            modele='Vente',
            objet_id=vente.id,
            details={
                'numero_vente': vente.numero_vente,
                'client': vente.client.nom if vente.client else 'Aucun',
                'montant_total': str(vente.montant_total),
                'lignes': len(lignes_data),
                'entrepots': [e.nom for e in entrepots_utilises]
            }
        )

        return vente


# serializers.py - Partie Transferts
# serializers.py - Ajoutez ces serializers

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
    """Serializer détaillé avec paiements et factures"""
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


class VenteCreateSerializer(serializers.ModelSerializer):
    """Ajouter les champs de paiement"""
    lignes_vente = LigneDeVenteCreateSerializer(many=True, write_only=True)
    mode_paiement = serializers.ChoiceField(
        choices=Vente.MODE_PAIEMENT, required=False)
    montant_paye = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=0)
    date_echeance = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = Vente
        fields = ('client', 'remise', 'lignes_vente', 'mode_paiement',
                  'montant_paye', 'date_echeance', 'notes')
        extra_kwargs = {
            'client': {'required': False, 'allow_null': True},
            'remise': {'required': False, 'default': 0}
        }

    @transaction.atomic
    def create(self, validated_data):
        lignes_data = validated_data.pop('lignes_vente')
        montant_paye = validated_data.pop('montant_paye', 0)
        mode_paiement = validated_data.pop('mode_paiement', None)

        # Générer numéro de vente
        today = datetime.now().strftime('%Y%m%d')
        last_vente_today = Vente.objects.filter(
            numero_vente__startswith=f'V{today}'
        ).count()
        numero_vente = f'V{today}{last_vente_today + 1:04d}'

        # Créer la vente
        vente = Vente.objects.create(
            numero_vente=numero_vente,
            created_by=self.context['request'].user,
            montant_paye=montant_paye,
            mode_paiement=mode_paiement,
            **validated_data
        )

        # Créer les lignes de vente
        entrepots_utilises = set()
        for ligne_data in lignes_data:
            ligne = LigneDeVente.objects.create(vente=vente, **ligne_data)
            entrepots_utilises.add(ligne.entrepot)

        # Ajouter les entrepôts utilisés
        vente.entrepots.set(entrepots_utilises)

        # Calculer le montant total
        vente.montant_total = vente.calculer_total()
        vente.save()

        # Si un paiement initial est fait, enregistrer
        if montant_paye > 0:
            Paiement.objects.create(
                vente=vente,
                montant=montant_paye,
                mode_paiement=mode_paiement or 'especes',
                created_by=self.context['request'].user
            )

        return vente
# serializers.py - Ajoutez ce serializer


class VenteUpdateSerializer(serializers.ModelSerializer):
    """Serializer pour la mise à jour des ventes"""
    lignes_vente = LigneDeVenteCreateSerializer(
        many=True, write_only=True, required=False)

    class Meta:
        model = Vente
        fields = ('client', 'remise', 'lignes_vente', 'mode_paiement',
                  'montant_paye', 'date_echeance', 'notes', 'statut')
        read_only_fields = ('created_by', 'created_at', 'numero_vente')

    def validate(self, data):
        """Validation pour la mise à jour"""
        vente = self.instance

        # Vérifier si la vente peut être modifiée
        if vente.statut not in ['brouillon']:
            raise serializers.ValidationError(
                "Seules les ventes en brouillon peuvent être modifiées"
            )

        # Validation des lignes de vente si fournies
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

                # Vérifier le stock dans l'entrepôt spécifié
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

        # Libérer les stocks des anciennes lignes si de nouvelles lignes sont fournies
        if lignes_data:
            for ancienne_ligne in instance.lignes_vente.all():
                try:
                    stock_entrepot = StockEntrepot.objects.get(
                        entrepot=ancienne_ligne.entrepot,
                        produit=ancienne_ligne.produit
                    )
                    stock_entrepot.liberer_stock(ancienne_ligne.quantite)
                except StockEntrepot.DoesNotExist:
                    pass

            # Supprimer les anciennes lignes
            instance.lignes_vente.all().delete()

            # Créer les nouvelles lignes
            entrepots_utilises = set()
            for ligne_data in lignes_data:
                ligne = LigneDeVente.objects.create(
                    vente=instance, **ligne_data)
                entrepots_utilises.add(ligne.entrepot)

                # Réserver le stock dans l'entrepôt
                stock_entrepot = StockEntrepot.objects.get(
                    produit=ligne.produit,
                    entrepot=ligne.entrepot
                )
                stock_entrepot.reserver_stock(ligne.quantite)

            # Mettre à jour les entrepôts
            instance.entrepots.set(entrepots_utilises)

        # Mettre à jour les autres champs
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        # Recalculer le montant total
        instance.montant_total = instance.calculer_total()
        instance.save()

        return instance

# serializers.py - Modifiez EnregistrerPaiementSerializer


class EnregistrerPaiementSerializer(serializers.Serializer):
    """Serializer pour enregistrer un paiement"""
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

        # Vérifier si le montant est supérieur à 0
        if montant <= 0:
            raise serializers.ValidationError({
                'montant': 'Le montant doit être supérieur à 0'
            })

        # Vérifier si le paiement dépasse le montant restant
        montant_restant = vente.montant_restant
        if montant > montant_restant:
            raise serializers.ValidationError({
                'montant': f"Le montant ({montant}) dépasse le montant restant ({montant_restant})"
            })

        # Vérifier que la vente est confirmée
        if vente.statut != 'confirmee':
            raise serializers.ValidationError({
                'non_field_errors': 'Seules les ventes confirmées peuvent recevoir des paiements'
            })

        # Vérifier que la vente n'est pas déjà entièrement payée
        if vente.statut_paiement == 'paye':
            raise serializers.ValidationError({
                'non_field_errors': 'Cette vente est déjà entièrement payée'
            })

        return data


class HistoriqueClientSerializer(serializers.Serializer):
    """Serializer pour l'historique d'un client"""
    ventes = VenteDetailSerializer(many=True, read_only=True)
    total_achats = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_paye = serializers.DecimalField(max_digits=12, decimal_places=2)
    solde_restant = serializers.DecimalField(max_digits=12, decimal_places=2)
    nombre_ventes = serializers.IntegerField()
    ventes_en_retard = serializers.IntegerField()
    dernier_achat = serializers.DateTimeField()


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
        """Calcule la quantité totale transférée"""
        return sum(ligne.quantite for ligne in obj.lignes_transfert.all())


class TransfertEntrepotCreateSerializer(serializers.ModelSerializer):
    """Serializer pour la création de transferts"""
    lignes_transfert = LigneTransfertCreateSerializer(
        many=True, write_only=True)

    class Meta:
        model = TransfertEntrepot
        fields = ('entrepot_source', 'entrepot_destination',
                  'motif', 'lignes_transfert')

    def validate(self, data):
        """Validation du transfert"""
        # Vérifier que les entrepôts sont différents
        if data['entrepot_source'] == data['entrepot_destination']:
            raise serializers.ValidationError({
                'entrepot_destination': "L'entrepôt source et destination doivent être différents"
            })

        # Vérifier les lignes de transfert
        if not data.get('lignes_transfert') or len(data['lignes_transfert']) == 0:
            raise serializers.ValidationError({
                'lignes_transfert': "Ajoutez au moins un produit au transfert"
            })

        # Vérifier les stocks
        for ligne in data['lignes_transfert']:
            produit = ligne['produit']
            quantite = ligne['quantite']

            # Vérifier la quantité
            if quantite <= 0:
                raise serializers.ValidationError({
                    'lignes_transfert': f"La quantité pour {produit.nom} doit être positive"
                })

            # Vérifier le stock
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

        # Générer une référence unique
        today = datetime.now().strftime('%Y%m%d')
        last_transfert_today = TransfertEntrepot.objects.filter(
            created_at__date=timezone.now().date()
        ).count()
        reference = f"TRF{today}{last_transfert_today + 1:04d}"

        # Créer le transfert
        transfert = TransfertEntrepot.objects.create(
            reference=reference,
            created_by=self.context['request'].user,
            **validated_data
        )

        # Créer les lignes de transfert
        for ligne_data in lignes_data:
            LigneTransfert.objects.create(transfert=transfert, **ligne_data)

        return transfert


# Serializer pour récupérer les stocks disponibles par produit
class StockDisponibleSerializer(serializers.Serializer):
    produit_id = serializers.IntegerField()

    def validate_produit_id(self, value):
        try:
            Produit.objects.get(id=value)
        except Produit.DoesNotExist:
            raise serializers.ValidationError("Produit non trouvé")
        return value


# Serializer pour le dashboard
class DashboardStatsSerializer(serializers.Serializer):
    total_ventes = serializers.IntegerField()
    chiffre_affaires = serializers.DecimalField(
        max_digits=12, decimal_places=2)
    total_clients = serializers.IntegerField()
    total_produits = serializers.IntegerField()
    total_entrepots = serializers.IntegerField()
    valeur_stock_total = serializers.DecimalField(
        max_digits=12, decimal_places=2)


# Serializer pour les rapports
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
