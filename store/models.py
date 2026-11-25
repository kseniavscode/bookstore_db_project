from django.db import models

class SystemUser(models.Model):
    username = models.CharField(max_length=50, unique=True)
    password_hash = models.CharField(max_length=255)
    role = models.CharField(max_length=7, default='Client')

    class Meta:
        managed = False
        db_table = 'system_user'

class City(models.Model):
    name = models.CharField(max_length=30)

    class Meta:
        managed = False
        db_table = 'city'

class Client(models.Model):
    user = models.ForeignKey(SystemUser, on_delete=models.CASCADE, db_column='user_id')
    city = models.ForeignKey(City, on_delete=models.RESTRICT, db_column='city_id')
    first_name = models.CharField(max_length=30)
    last_name = models.CharField(max_length=45)
    middle_name = models.CharField(max_length=40, blank=True, null=True)
    phone = models.CharField(max_length=12, blank=True, null=True)
    birthdate = models.DateField(blank=True, null=True)
    street = models.CharField(max_length=50, blank=True, null=True)
    num_house = models.CharField(max_length=6, blank=True, null=True)
    num_apartment = models.CharField(max_length=7, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'client'

class Genre(models.Model):
    name = models.CharField(max_length=32)

    class Meta:
        managed = False
        db_table = 'genre'

class Language(models.Model):
    name = models.CharField(max_length=25)

    class Meta:
        managed = False
        db_table = 'language'

class Imprint(models.Model):
    name = models.CharField(max_length=50)

    class Meta:
        managed = False
        db_table = 'imprint'

class Author(models.Model):
    first_name = models.CharField(max_length=30)
    last_name = models.CharField(max_length=30)
    biography = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'author'

class Book(models.Model):
    isbn = models.CharField(max_length=13, primary_key=True)
    language = models.ForeignKey(Language, on_delete=models.RESTRICT, db_column='language_id')
    imprint = models.ForeignKey(Imprint, on_delete=models.RESTRICT, db_column='imprint_id')
    title = models.CharField(max_length=150)
    summary = models.CharField(max_length=500, blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    cover = models.BinaryField(blank=True, null=True)  # <-- Добавили обложку BYTEA
    page_count = models.IntegerField(blank=True, null=True)
    publication_year = models.IntegerField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)
    quantity = models.IntegerField(default=0)

    class Meta:
        managed = False
        db_table = 'book'

class BookAuthor(models.Model):
    book = models.ForeignKey(Book, on_delete=models.CASCADE, db_column='book_id')
    author = models.ForeignKey(Author, on_delete=models.CASCADE, db_column='author_id')

    class Meta:
        managed = False
        db_table = 'book_author'

class BookGenre(models.Model):
    book = models.ForeignKey(Book, on_delete=models.CASCADE, db_column='book_id')
    genre = models.ForeignKey(Genre, on_delete=models.CASCADE, db_column='genre_id')

    class Meta:
        managed = False
        db_table = 'book_genre'

class Order(models.Model):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, db_column='client_id')
    city = models.ForeignKey(City, on_delete=models.RESTRICT, db_column='city_id')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=10, default='new')
    delivery_street = models.CharField(max_length=50, blank=True, null=True)
    delivery_num_house = models.CharField(max_length=6, blank=True, null=True)
    delivery_num_apartment = models.CharField(max_length=7, blank=True, null=True)
    delivery_method = models.CharField(max_length=7, default='pickup')

    class Meta:
        managed = False
        db_table = 'order'

class OrderBook(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, db_column='order_id')
    book = models.ForeignKey(Book, on_delete=models.CASCADE, db_column='book_id')
    quantity = models.IntegerField(default=1)
    price_at_time = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        managed = False
        db_table = 'order_book'

class WaitList(models.Model):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, db_column='client_id')
    book = models.ForeignKey(Book, on_delete=models.CASCADE, db_column='book_id')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'waitlist'

class AuditLog(models.Model):
    user = models.ForeignKey(SystemUser, on_delete=models.SET_NULL, null=True, db_column='user_id')
    action = models.CharField(max_length=50)
    timestamp = models.DateTimeField(auto_now_add=True)
    object_id = models.IntegerField(blank=True, null=True)
    object_type = models.CharField(max_length=50, blank=True, null=True)
    details = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'audit_log'