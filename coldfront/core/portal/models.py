from django.db import models

class Carousel(models.Model):
    title = models.CharField(max_length=100)
    description = models.TextField()
    image = models.ImageField(upload_to='carousel/')
    link = models.URLField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.title