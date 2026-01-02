# signals.py
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import *


@receiver(post_save, sender=Produit)
def log_produit_save(sender, instance, created, **kwargs):
    action = 'creation' if created else 'modification'
    AuditLog.objects.create(
        user=instance.created_by if created else None,  # À adapter selon votre logique
        action=action,
        modele='Produit',
        objet_id=instance.id,
        details={
            'nom': instance.nom,
            'code': instance.code,
            'prix_vente': str(instance.prix_vente),
            'created': created
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
                'montant_total': str(instance.montant_total)
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
                'motif': instance.motif
            }
        )
