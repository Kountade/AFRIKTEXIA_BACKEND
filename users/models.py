from django.db.models.signals import post_save, post_delete
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.base_user import BaseUserManager
from django.db.models import Sum
from django_rest_passwordreset.signals import reset_password_token_created
from django.dispatch import receiver
from django.template.loader import render_to_string
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags
from django.utils import timezone
from django.db import transaction
from django.db.models import Q


class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is a required field')

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'admin')
        return self.create_user(email, password, **extra_fields)


class CustomUser(AbstractUser):
    ROLE_CHOICES = (
        ('admin', 'Admin'),
        ('vendeur', 'Vendeur'),
    )

    email = models.EmailField(max_length=200, unique=True)
    birthday = models.DateField(null=True, blank=True)
    username = models.CharField(max_length=200, null=True, blank=True)
    role = models.CharField(
        max_length=20, choices=ROLE_CHOICES, default='vendeur')
    telephone = models.CharField(max_length=20, blank=True)
    adresse = models.TextField(blank=True)

    objects = CustomUserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    def __str__(self):
        return f"{self.email} ({self.role})"


class Categorie(models.Model):
    nom = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def nombre_produits(self):
        return self.produit_set.count()

    def __str__(self):
        return self.nom


class Fournisseur(models.Model):
    nom = models.CharField(max_length=200)
    contact = models.CharField(max_length=100)
    telephone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    adresse = models.TextField()
    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nom


class Produit(models.Model):
    code = models.CharField(max_length=50, unique=True)
    nom = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    categorie = models.ForeignKey(
        Categorie, on_delete=models.SET_NULL, null=True)
    prix_achat = models.DecimalField(max_digits=10, decimal_places=2)
    prix_vente = models.DecimalField(max_digits=10, decimal_places=2)
    stock_alerte = models.IntegerField(default=5)
    fournisseur = models.ForeignKey(
        Fournisseur, on_delete=models.SET_NULL, null=True)
    image = models.ImageField(
        upload_to='produits/images/',
        null=True,
        blank=True,
        verbose_name='Image du produit'
    )
    thumbnail = models.ImageField(
        upload_to='produits/thumbnails/',
        null=True,
        blank=True,
        editable=False
    )
    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def stock_actuel(self):
        """Stock total dans tous les entrepôts"""
        total = StockEntrepot.objects.filter(produit=self).aggregate(
            total=Sum('quantite')
        )['total'] or 0
        return total

    def stock_reserve(self):
        """Stock réservé dans tous les entrepôts"""
        total = StockEntrepot.objects.filter(produit=self).aggregate(
            total=Sum('quantite_reservee')
        )['total'] or 0
        return total

    @property
    def stock_disponible(self):
        """Stock disponible pour vente"""
        return self.stock_actuel() - self.stock_reserve()

    @property
    def en_rupture(self):
        return self.stock_disponible <= 0

    @property
    def stock_faible(self):
        return 0 < self.stock_disponible <= self.stock_alerte

    def __str__(self):
        return f"{self.nom} ({self.code})"


class Client(models.Model):
    TYPE_CLIENT_CHOICES = (
        ('particulier', 'Particulier'),
        ('professionnel', 'Professionnel'),
    )

    nom = models.CharField(max_length=200)
    type_client = models.CharField(
        max_length=20, choices=TYPE_CLIENT_CHOICES, default='particulier')
    telephone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    adresse = models.TextField()
    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.nom


class Entrepot(models.Model):
    nom = models.CharField(max_length=200)
    adresse = models.TextField()
    telephone = models.CharField(max_length=20, blank=True)
    responsable = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, related_name='entrepots_geres'
    )
    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    actif = models.BooleanField(default=True)

    class Meta:
        ordering = ['nom']
        verbose_name_plural = 'Entrepôts'

    def stock_total_valeur(self):
        stocks = StockEntrepot.objects.filter(entrepot=self)
        total = 0
        for stock in stocks:
            total += stock.quantite * stock.produit.prix_achat
        return total

    def produits_count(self):
        return StockEntrepot.objects.filter(entrepot=self).count()

    def __str__(self):
        return f"{self.nom}"


class StockEntrepot(models.Model):
    entrepot = models.ForeignKey(Entrepot, on_delete=models.CASCADE)
    produit = models.ForeignKey(Produit, on_delete=models.CASCADE)
    quantite = models.IntegerField(default=0)
    quantite_reservee = models.IntegerField(default=0)
    stock_alerte = models.IntegerField(default=5)
    emplacement = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['entrepot', 'produit']
        ordering = ['produit__nom']

    @property
    def quantite_disponible(self):
        """Quantité réellement disponible pour vente"""
        disponible = self.quantite - self.quantite_reservee
        return max(0, disponible)

    @property
    def en_rupture(self):
        return self.quantite_disponible <= 0

    @property
    def stock_faible(self):
        return 0 < self.quantite_disponible <= self.stock_alerte

    def reserver_stock(self, quantite):
        """Réserver du stock pour une vente"""
        if quantite <= 0:
            raise ValueError("Quantité doit être positive")

        disponible = self.quantite_disponible
        if quantite > disponible:
            raise ValueError(
                f"Stock disponible insuffisant dans cet entrepôt: {disponible} unités disponibles"
            )

        with transaction.atomic():
            # Utiliser F() pour éviter les problèmes de concurrence
            StockEntrepot.objects.filter(id=self.id).update(
                quantite_reservee=models.F('quantite_reservee') + quantite,
                updated_at=timezone.now()
            )
            self.refresh_from_db()

    def liberer_stock(self, quantite):
        """Libérer du stock réservé"""
        if quantite <= 0:
            raise ValueError("Quantité doit être positive")

        with transaction.atomic():
            # S'assurer qu'on ne libère pas plus que ce qui est réservé
            StockEntrepot.objects.filter(id=self.id).update(
                quantite_reservee=models.F('quantite_reservee') - quantite,
                updated_at=timezone.now()
            )
            self.refresh_from_db()

    def prelever_stock(self, quantite):
        """Prélever du stock (confirmer une vente)"""
        if quantite <= 0:
            raise ValueError("Quantité doit être positive")

        with transaction.atomic():
            # Recharger l'objet avec verrouillage
            stock = StockEntrepot.objects.select_for_update().get(id=self.id)

            # Vérifier qu'on a assez de stock réservé
            if quantite > stock.quantite_reservee:
                raise ValueError(
                    f"Quantité à prélever ({quantite}) supérieure au stock réservé ({stock.quantite_reservee})"
                )

            # Mettre à jour les quantités
            StockEntrepot.objects.filter(id=self.id).update(
                quantite=models.F('quantite') - quantite,
                quantite_reservee=models.F('quantite_reservee') - quantite,
                updated_at=timezone.now()
            )
            self.refresh_from_db()

    def __str__(self):
        return f"{self.produit.nom} - {self.entrepot.nom}: {self.quantite_disponible} disponible(s)"


class MouvementStock(models.Model):
    TYPE_MOUVEMENT = (
        ('entree', 'Entrée en stock'),
        ('sortie', 'Sortie de stock'),
        ('ajustement', 'Ajustement'),
        ('transfert', 'Transfert entrepôt'),
    )

    produit = models.ForeignKey(Produit, on_delete=models.CASCADE)
    type_mouvement = models.CharField(max_length=20, choices=TYPE_MOUVEMENT)
    quantite = models.IntegerField()
    prix_unitaire = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    motif = models.TextField()
    entrepot = models.ForeignKey(
        Entrepot, on_delete=models.CASCADE, null=True, blank=True)
    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.prix_unitaire:
            if self.type_mouvement == 'entree':
                self.prix_unitaire = self.produit.prix_achat
            else:
                self.prix_unitaire = self.produit.prix_vente
        super().save(*args, **kwargs)

    def __str__(self):
        entrepot_str = f" ({self.entrepot.nom})" if self.entrepot else ""
        return f"{self.produit.nom} - {self.type_mouvement}{entrepot_str} ({self.quantite})"


class Vente(models.Model):
    STATUT_VENTE = (
        ('brouillon', 'Brouillon'),
        ('confirmee', 'Confirmée'),
        ('annulee', 'Annulée'),
    )

    STATUT_PAIEMENT = (
        ('non_paye', 'Non payé'),
        ('partiel', 'Payé partiellement'),
        ('paye', 'Payé'),
        ('retard', 'En retard'),
    )

    MODE_PAIEMENT = (
        ('especes', 'Espèces'),
        ('carte_bancaire', 'Carte bancaire'),
        ('cheque', 'Chèque'),
        ('virement', 'Virement'),
        ('mobile_money', 'Mobile Money'),
    )

    client = models.ForeignKey(
        Client, on_delete=models.SET_NULL, null=True, blank=True
    )
    numero_vente = models.CharField(max_length=50, unique=True)
    statut = models.CharField(
        max_length=20, choices=STATUT_VENTE, default='brouillon'
    )
    statut_paiement = models.CharField(
        max_length=20, choices=STATUT_PAIEMENT, default='non_paye'
    )
    mode_paiement = models.CharField(
        max_length=20, choices=MODE_PAIEMENT, null=True, blank=True
    )
    montant_total = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    montant_paye = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    montant_restant = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    remise = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    date_echeance = models.DateField(null=True, blank=True)
    date_paiement = models.DateTimeField(null=True, blank=True)
    entrepots = models.ManyToManyField(Entrepot, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        # Calculer le montant restant
        self.montant_restant = self.montant_total - self.montant_paye

        # Mettre à jour le statut de paiement
        if self.montant_paye == 0:
            self.statut_paiement = 'non_paye'
        elif self.montant_paye < self.montant_total:
            self.statut_paiement = 'partiel'
        else:
            self.statut_paiement = 'paye'
            self.date_paiement = timezone.now()

        super().save(*args, **kwargs)

    def calculer_total(self):
        total = sum(detail.sous_total() for detail in self.lignes_vente.all())
        return total - self.remise

    def pourcentage_paye(self):
        if self.montant_total == 0:
            return 0
        return (self.montant_paye / self.montant_total) * 100

    def jours_retard(self):
        if self.date_echeance and self.statut_paiement != 'paye':
            if timezone.now().date() > self.date_echeance:
                return (timezone.now().date() - self.date_echeance).days
        return 0

    def confirmer_vente(self):
        """Confirmer la vente et prélever les stocks"""
        if self.statut != 'brouillon':
            raise ValueError(
                "Seules les ventes brouillon peuvent être confirmées")

        with transaction.atomic():
            self.statut = 'confirmee'
            self.save()

            # Prélever le stock pour chaque ligne de vente
            for ligne in self.lignes_vente.all():
                ligne.prelever_stock_entrepot()

                # Créer le mouvement de stock
                MouvementStock.objects.create(
                    produit=ligne.produit,
                    type_mouvement='sortie',
                    quantite=ligne.quantite,
                    prix_unitaire=ligne.prix_unitaire,
                    motif=f"Vente {self.numero_vente}" +
                    (f" - Client: {self.client.nom}" if self.client else ""),
                    entrepot=ligne.entrepot,
                    created_by=self.created_by
                )

        # Log d'audit
        AuditLog.objects.create(
            user=self.created_by,
            action='vente',
            modele='Vente',
            objet_id=self.id,
            details={
                'action': 'confirmation',
                'numero_vente': self.numero_vente,
                'client': self.client.nom if self.client else 'Aucun',
                'mouvements_crees': self.lignes_vente.count()
            }
        )

    def update_statut_paiement(self):
        """Mettre à jour le statut de paiement"""
        if self.montant_paye == 0:
            self.statut_paiement = 'non_paye'
        elif self.montant_paye >= self.montant_total:
            self.statut_paiement = 'paye'
            self.date_paiement = timezone.now()
        else:
            self.statut_paiement = 'partiel'
        self.save()

    def ajouter_paiement(self, montant, mode_paiement, reference='', notes='', user=None):
        """Ajouter un paiement à la vente"""
        with transaction.atomic():
            # Créer l'objet Paiement
            paiement = Paiement.objects.create(
                vente=self,
                montant=montant,
                mode_paiement=mode_paiement,
                reference=reference,
                notes=notes,
                created_by=user or self.created_by
            )

            # Mettre à jour le montant payé
            self.montant_paye += montant

            # Si la vente n'a pas de mode de paiement principal, utiliser celui du premier paiement
            if not self.mode_paiement:
                self.mode_paiement = mode_paiement

            # Mettre à jour le statut de paiement
            self.update_statut_paiement()

        # Log d'audit
        AuditLog.objects.create(
            user=user or self.created_by,
            action='vente',
            modele='Paiement',
            objet_id=paiement.id,
            details={
                'vente': self.numero_vente,
                'montant': str(montant),
                'mode_paiement': mode_paiement,
                'nouveau_statut': self.statut_paiement
            }
        )

        return paiement


class Paiement(models.Model):
    vente = models.ForeignKey(
        Vente, on_delete=models.CASCADE, related_name='paiements')
    montant = models.DecimalField(max_digits=12, decimal_places=2)
    mode_paiement = models.CharField(
        max_length=20, choices=Vente.MODE_PAIEMENT)
    reference = models.CharField(max_length=100, blank=True)
    date_paiement = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['-date_paiement']

    def __str__(self):
        return f"Paiement de {self.montant}€ pour {self.vente.numero_vente}"


class Facture(models.Model):
    vente = models.OneToOneField(
        Vente, on_delete=models.CASCADE, related_name='facture')
    numero_facture = models.CharField(max_length=50, unique=True)
    date_facture = models.DateField(auto_now_add=True)
    montant_ht = models.DecimalField(
        max_digits=12, decimal_places=2, default=0)
    tva = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    montant_ttc = models.DecimalField(
        max_digits=12, decimal_places=2, default=0)
    pdf_facture = models.FileField(
        upload_to='factures/', null=True, blank=True)
    envoye_email = models.BooleanField(default=False)
    date_envoi = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Facture {self.numero_facture} - {self.vente.numero_vente}"


class LigneDeVente(models.Model):
    vente = models.ForeignKey(
        Vente, on_delete=models.CASCADE, related_name='lignes_vente'
    )
    produit = models.ForeignKey(Produit, on_delete=models.CASCADE)
    entrepot = models.ForeignKey(Entrepot, on_delete=models.CASCADE)
    quantite = models.IntegerField()
    prix_unitaire = models.DecimalField(max_digits=10, decimal_places=2)
    stock_preleve = models.BooleanField(default=False)

    def sous_total(self):
        return self.quantite * self.prix_unitaire

    def prelever_stock_entrepot(self):
        """Prélever le stock de l'entrepôt"""
        if not self.stock_preleve:
            try:
                stock_entrepot = StockEntrepot.objects.get(
                    entrepot=self.entrepot,
                    produit=self.produit
                )
                stock_entrepot.prelever_stock(self.quantite)
                self.stock_preleve = True
                self.save()
            except StockEntrepot.DoesNotExist:
                raise ValueError(
                    f"Stock non trouvé pour {self.produit.nom} dans {self.entrepot.nom}")

    def __str__(self):
        return f"{self.produit.nom} x{self.quantite} ({self.entrepot.nom})"


class TransfertEntrepot(models.Model):
    STATUT_TRANSFERT = (
        ('brouillon', 'Brouillon'),
        ('confirme', 'Confirmé'),
        ('annule', 'Annulé'),
    )

    reference = models.CharField(max_length=50, unique=True)
    entrepot_source = models.ForeignKey(
        Entrepot, on_delete=models.CASCADE, related_name='transferts_sortants'
    )
    entrepot_destination = models.ForeignKey(
        Entrepot, on_delete=models.CASCADE, related_name='transferts_entrants'
    )
    statut = models.CharField(
        max_length=20, choices=STATUT_TRANSFERT, default='brouillon'
    )
    motif = models.TextField(blank=True)
    created_by = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    confirme_at = models.DateTimeField(null=True, blank=True)

    def confirmer_transfert(self):
        """Confirmer le transfert et mettre à jour les stocks"""
        if self.statut == 'brouillon':
            with transaction.atomic():
                for ligne in self.lignes_transfert.all():
                    # Réduire le stock source
                    stock_source = StockEntrepot.objects.get(
                        entrepot=self.entrepot_source,
                        produit=ligne.produit
                    )
                    stock_source.quantite -= ligne.quantite
                    stock_source.save()

                    # Augmenter le stock destination
                    stock_dest, created = StockEntrepot.objects.get_or_create(
                        entrepot=self.entrepot_destination,
                        produit=ligne.produit,
                        defaults={'quantite': 0}
                    )
                    stock_dest.quantite += ligne.quantite
                    stock_dest.save()

                    # Créer un mouvement de stock
                    MouvementStock.objects.create(
                        produit=ligne.produit,
                        type_mouvement='transfert',
                        quantite=ligne.quantite,
                        prix_unitaire=ligne.produit.prix_achat,
                        motif=f"Transfert {self.reference}",
                        created_by=self.created_by
                    )

                self.statut = 'confirme'
                self.confirme_at = timezone.now()
                self.save()


class LigneTransfert(models.Model):
    transfert = models.ForeignKey(
        TransfertEntrepot, on_delete=models.CASCADE, related_name='lignes_transfert'
    )
    produit = models.ForeignKey(Produit, on_delete=models.CASCADE)
    quantite = models.IntegerField()

    def __str__(self):
        return f"{self.produit.nom} x{self.quantite}"


class AuditLog(models.Model):
    ACTION_CHOICES = (
        ('creation', 'Création'),
        ('modification', 'Modification'),
        ('suppression', 'Suppression'),
        ('vente', 'Vente'),
        ('mouvement_stock', 'Mouvement de stock'),
        ('connexion', 'Connexion'),
        ('deconnexion', 'Déconnexion'),
    )

    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    modele = models.CharField(max_length=100)
    objet_id = models.IntegerField(null=True, blank=True)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} - {self.action} - {self.modele} #{self.objet_id}"


# Signaux pour la traçabilité
@receiver(post_save, sender=Produit)
def log_produit_save(sender, instance, created, **kwargs):
    action = 'creation' if created else 'modification'
    AuditLog.objects.create(
        user=instance.created_by,
        action=action,
        modele='Produit',
        objet_id=instance.id,
        details={
            'nom': instance.nom,
            'code': instance.code,
            'prix_vente': str(instance.prix_vente),
        }
    )


@receiver(post_save, sender=Vente)
def log_vente(sender, instance, created, **kwargs):
    if created:
        AuditLog.objects.create(
            user=instance.created_by,
            action='vente',
            modele='Vente',
            objet_id=instance.id,
            details={
                'numero_vente': instance.numero_vente,
                'client': instance.client.nom if instance.client else 'Aucun',
                'statut': instance.statut
            }
        )


@receiver(post_save, sender=MouvementStock)
def log_mouvement_stock(sender, instance, created, **kwargs):
    if created:
        AuditLog.objects.create(
            user=instance.created_by,
            action='mouvement_stock',
            modele='MouvementStock',
            objet_id=instance.id,
            details={
                'produit': instance.produit.nom,
                'type': instance.type_mouvement,
                'quantite': instance.quantite,
            }
        )


@receiver(post_save, sender=Client)
def log_client_save(sender, instance, created, **kwargs):
    action = 'creation' if created else 'modification'
    AuditLog.objects.create(
        user=instance.created_by,
        action=action,
        modele='Client',
        objet_id=instance.id,
        details={
            'nom': instance.nom,
            'type_client': instance.type_client,
        }
    )


# Signal pour le reset de password
@receiver(reset_password_token_created)
def password_reset_token_created(reset_password_token, *args, **kwargs):
    sitelink = "http://localhost:5173/"
    token = "{}".format(reset_password_token.key)
    full_link = str(sitelink) + str("password-reset/") + str(token)

    context = {
        'full_link': full_link,
        'email_address': reset_password_token.user.email
    }

    html_message = render_to_string("backend/email.html", context=context)
    plain_message = strip_tags(html_message)

    msg = EmailMultiAlternatives(
        subject=f"Réinitialisation de mot de passe pour {reset_password_token.user.email}",
        body=plain_message,
        from_email="codelivecamp@gmail.com",
        to=[reset_password_token.user.email]
    )

    msg.attach_alternative(html_message, "text/html")
    msg.send()
