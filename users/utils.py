# utils.py dans votre application Django
import os
from PIL import Image, ImageOps
from io import BytesIO
from django.core.files.base import ContentFile
from django.conf import settings


def generate_thumbnail(image_field, size=(150, 150)):
    """Génère une miniature d'une image"""
    if not image_field:
        return None

    try:
        img = Image.open(image_field)

        # Convertir en RGB si nécessaire
        if img.mode not in ('L', 'RGB'):
            img = img.convert('RGB')

        # Créer une miniature
        img = ImageOps.fit(img, size, Image.LANCZOS)

        # Sauvegarder dans un buffer
        buffer = BytesIO()
        img.save(buffer, format='JPEG', quality=85)
        buffer.seek(0)

        # Créer un nom de fichier
        filename = os.path.splitext(image_field.name)[0]
        thumbnail_filename = f"{filename}_thumb.jpg"

        return ContentFile(buffer.getvalue(), name=thumbnail_filename)
    except Exception as e:
        print(f"Erreur lors de la génération de la miniature: {e}")
        return None


def resize_image(image_field, max_size=(800, 800)):
    """Redimensionne une image pour ne pas dépasser les dimensions max"""
    if not image_field:
        return None

    try:
        img = Image.open(image_field)

        # Vérifier si le redimensionnement est nécessaire
        if img.width <= max_size[0] and img.height <= max_size[1]:
            return image_field

        # Calculer les nouvelles dimensions
        ratio = min(max_size[0] / img.width, max_size[1] / img.height)
        new_width = int(img.width * ratio)
        new_height = int(img.height * ratio)

        # Redimensionner
        img = img.resize((new_width, new_height), Image.LANCZOS)

        # Sauvegarder
        buffer = BytesIO()

        # Conserver le format original si possible
        if image_field.name.lower().endswith('.png'):
            img.save(buffer, format='PNG', optimize=True)
        else:
            img = img.convert('RGB') if img.mode != 'RGB' else img
            img.save(buffer, format='JPEG', quality=85, optimize=True)

        buffer.seek(0)

        return ContentFile(buffer.getvalue(), name=image_field.name)
    except Exception as e:
        print(f"Erreur lors du redimensionnement de l'image: {e}")
        return image_field
