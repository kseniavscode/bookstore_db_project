from django.urls import path
from . import views

urlpatterns = [
    path('', views.catalog_view, name='catalog'),
    path('login/', views.login_view, name='login'),
    path('signup/', views.signup_view, name='signup'),
    path('logout/', views.logout_view, name='logout'),

    path('cart/', views.cart_view, name='cart'),
    path('cart/add/<str:isbn>/', views.add_to_cart_view, name='add_to_cart'),
    path('cart/remove/<int:item_id>/', views.remove_from_cart_view, name='remove_from_cart'),
    path('cart/checkout/', views.checkout_view, name='checkout'),
    path('waitlist/add/<str:isbn>/', views.add_to_waitlist_view, name='add_to_waitlist'),

    path('staff/', views.staff_view, name='staff'),
    path('orders/', views.orders_view, name='orders'),
    path('books/manage/', views.books_manage_view, name='books_manage'),
    path('book-cover/<str:isbn>/', views.book_cover_view, name='book_cover'),
    path('api/lookup-isbn/', views.lookup_isbn_view, name='lookup_isbn'),
    path('analytics/', views.analytics_view, name='analytics'),

    path('audit/', views.audit_view, name='audit'),
    path('audit/undo/<int:log_id>/', views.undo_audit_view, name='undo_audit'),
]