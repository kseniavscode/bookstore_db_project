import re
from django.shortcuts import render, redirect
from django.db import connection, IntegrityError, DatabaseError
from django.utils import timezone
from .models import (
    SystemUser, Client, City, Book, Author, Genre, Language, Imprint,
    BookAuthor, BookGenre, Order, OrderBook, WaitList, AuditLog
)

from django.core.paginator import Paginator
import json
import urllib.request
from django.http import HttpResponse, JsonResponse

import json
import re
import urllib.request
import urllib.parse
import urllib.error

def log_action(user_id, action, object_type, details, object_id=None):
    AuditLog.objects.create(
        user_id=user_id,
        action=action,
        object_id=object_id,
        object_type=object_type,
        details=details[:250]
    )

def catalog_view(request):
    query = request.GET.get('q', '').strip()
    page_num = request.GET.get('page', 1)
    per_page = 20

    message = request.session.pop('flash_msg', None)
    error = request.session.pop('flash_err', None)

    with connection.cursor() as cursor:
        if query:
            cursor.execute(
                'SELECT COUNT(*) FROM v_book_catalog WHERE "Название" ILIKE %s',
                [f'%{query}%']
            )
        else:
            cursor.execute('SELECT COUNT(*) FROM v_book_catalog')
        total_books = cursor.fetchone()[0]

        paginator = Paginator(range(total_books), per_page)
        page_obj = paginator.get_page(page_num)
        offset = (page_obj.number - 1) * per_page
        page_range = paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1)

        if query:
            cursor.execute(
                """SELECT isbn, "Название", "Автор(ы)", "Издательство", "Язык", "Цена", "Остаток"
                   FROM v_book_catalog
                   WHERE "Название" ILIKE %s
                   ORDER BY "Название" ASC
                   LIMIT %s OFFSET %s""",
                [f'%{query}%', per_page, offset]
            )
        else:
            cursor.execute(
                """SELECT isbn, "Название", "Автор(ы)", "Издательство", "Язык", "Цена", "Остаток"
                   FROM v_book_catalog
                   ORDER BY "Название" ASC
                   LIMIT %s OFFSET %s""",
                [per_page, offset]
            )
        books = cursor.fetchall()

    isbns = [b[0] for b in books]
    has_cover_set = set(
        Book.objects.filter(isbn__in=isbns, cover__isnull=False).values_list('isbn', flat=True)
    )

    return render(request, 'store/catalog.html', {
        'books': books,
        'has_cover_set': has_cover_set,
        'query': query,
        'total_books': total_books,
        'page_obj': page_obj,
        'page_range': page_range,
        'ellipsis': paginator.ELLIPSIS,
        'message': message,
        'error': error
    })

# 2. АВТОРИЗАЦИЯ И РЕГИСТРАЦИЯ
def login_view(request):
    error = None
    if request.method == 'POST':
        username_input = request.POST.get('username', '').strip()
        password_input = request.POST.get('password', '').strip()

        try:
            user = SystemUser.objects.get(username=username_input, password_hash=password_input)
            request.session['user_id'] = user.id
            request.session['username'] = user.username
            request.session['role'] = user.role

            log_action(user.id, 'LOGIN', 'system_user', f'Вход пользователя {user.username} ({user.role})', user.id)
            return redirect('catalog')
        except SystemUser.DoesNotExist:
            error = 'Неверный логин или пароль.'

    return render(request, 'store/login.html', {'error': error})

# Замени signup_view (регистрация + автоматический вход в аккаунт)
def signup_view(request):
    error = None
    cities = City.objects.all().order_by('name')

    if request.method == 'POST':
        username_input = request.POST.get('username', '').strip()
        password_input = request.POST.get('password', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        phone = request.POST.get('phone', '').strip().replace('+', '')[:12]
        city_id = request.POST.get('city_id')
        street = request.POST.get('street', '').strip()
        num_house = request.POST.get('num_house', '').strip()

        try:
            if SystemUser.objects.filter(username=username_input).exists():
                error = 'Такой логин уже занят.'
            else:
                new_user = SystemUser.objects.create(
                    username=username_input,
                    password_hash=password_input,
                    role='Client'
                )
                Client.objects.create(
                    user=new_user,
                    city_id=city_id,
                    first_name=first_name,
                    last_name=last_name,
                    phone=phone,
                    street=street or None,
                    num_house=num_house or None
                )
                log_action(new_user.id, 'INSERT', 'system_user', f'Регистрация клиента {username_input} ({first_name} {last_name})', new_user.id)
                log_action(new_user.id, 'LOGIN', 'system_user', f'Автоматический вход после регистрации ({username_input})', new_user.id)

                # Сразу входим в аккаунт через сессию
                request.session['user_id'] = new_user.id
                request.session['username'] = new_user.username
                request.session['role'] = new_user.role
                request.session['flash_msg'] = f'Добро пожаловать, {first_name}! Регистрация завершена, вы вошли в аккаунт.'
                return redirect('catalog')
        except (IntegrityError, DatabaseError) as e:
            error = f'Ошибка базы данных: {str(e)}'

    return render(request, 'store/signup.html', {'error': error, 'cities': cities})

def logout_view(request):
    request.session.flush()
    return redirect('catalog')

# 3. КОРЗИНА И ЛИСТ ОЖИДАНИЯ (Только для авторизованных)
def cart_view(request):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    client = Client.objects.filter(user_id=user_id).first()
    message = request.session.pop('flash_msg', None)
    error = request.session.pop('flash_err', None)

    if not client:
        return render(request, 'store/cart.html', {
            'no_profile': True,
            'message': message,
            'error': error
        })

    # Активная корзина — заказ со статусом 'new'
    active_order = Order.objects.filter(client=client, status='new').first()
    items = []
    total_sum = 0

    if active_order:
        order_items = OrderBook.objects.filter(order=active_order).select_related('book')
        for item in order_items:
            subtotal = item.quantity * item.price_at_time
            total_sum += subtotal
            items.append({
                'id': item.id,
                'book': item.book,
                'quantity': item.quantity,
                'price': item.price_at_time,
                'subtotal': subtotal
            })

    # Прошлые оформленные заказы клиента + лист ожидания
    past_orders = Order.objects.filter(client=client).exclude(status='new').order_by('-created_at')
    waitlists = WaitList.objects.filter(client=client).select_related('book')

    return render(request, 'store/cart.html', {
        'active_order': active_order,
        'items': items,
        'total_sum': total_sum,
        'past_orders': past_orders,
        'waitlists': waitlists,
        'message': message,
        'error': error
    })

def add_to_cart_view(request, isbn):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    client = Client.objects.filter(user_id=user_id).first()
    if not client:
        request.session['flash_err'] = 'У вашей учетной записи нет профиля клиента для оформления покупок.'
        return redirect('catalog')

    try:
        book = Book.objects.get(isbn=isbn)
        if book.quantity <= 0:
            request.session['flash_err'] = 'Этой книги сейчас нет в наличии.'
            return redirect('catalog')

        order, created = Order.objects.get_or_create(
            client=client,
            status='new',
            defaults={
                'city': client.city,
                'delivery_method': 'pickup',
                'delivery_street': client.street,
                'delivery_num_house': client.num_house,
                'delivery_num_apartment': client.num_apartment
            }
        )
        if created:
            log_action(user_id, 'INSERT', 'order', f'Создана корзина (заказ #{order.id})', order.id)

        ob, ob_created = OrderBook.objects.get_or_create(
            order=order,
            book=book,
            defaults={'quantity': 1, 'price_at_time': book.price}
        )
        if not ob_created:
            ob.quantity += 1
            ob.price_at_time = book.price
            ob.save()
            log_action(user_id, 'UPDATE', 'order_book', f'Увеличено кол-во книги {book.title} в заказе #{order.id}', ob.id)
        else:
            log_action(user_id, 'INSERT', 'order_book', f'Добавлена книга {book.title} в корзину (заказ #{order.id})', ob.id)

        request.session['flash_msg'] = f'Книга «{book.title}» добавлена в корзину!'
    except Exception as e:
        request.session['flash_err'] = f'Ошибка добавления в корзину: {e}'

    return redirect('catalog')

def remove_from_cart_view(request, item_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    try:
        ob = OrderBook.objects.get(id=item_id)
        title = ob.book.title
        order_id = ob.order_id
        ob.delete()
        log_action(user_id, 'DELETE', 'order_book', f'Удалена книга {title} из корзины (заказ #{order_id})', item_id)
        request.session['flash_msg'] = 'Позиция удалена из корзины.'
    except Exception as e:
        request.session['flash_err'] = f'Ошибка удаления: {e}'

    return redirect('cart')

def checkout_view(request):
    user_id = request.session.get('user_id')
    if not user_id or request.method != 'POST':
        return redirect('login')

    client = Client.objects.filter(user_id=user_id).first()
    active_order = Order.objects.filter(client=client, status='new').first()
    if not active_order:
        return redirect('cart')

    delivery_method = request.POST.get('delivery_method', 'pickup')
    try:
        active_order.status = 'processing'
        active_order.delivery_method = delivery_method
        active_order.updated_at = timezone.now()
        active_order.save()

        # Уменьшаем остаток книг на складе
        for item in OrderBook.objects.filter(order=active_order):
            book = item.book
            if book.quantity >= item.quantity:
                book.quantity -= item.quantity
                book.save()

        log_action(user_id, 'UPDATE', 'order', f'Клиент оформил заказ #{active_order.id} (статус: processing)', active_order.id)
        request.session['flash_msg'] = f'Заказ #{active_order.id} успешно оформлен!'
    except Exception as e:
        request.session['flash_err'] = f'Ошибка оформления заказа: {e}'

    return redirect('cart')

def add_to_waitlist_view(request, isbn):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    client = Client.objects.filter(user_id=user_id).first()
    if not client:
        request.session['flash_err'] = 'Не найден профиль клиента.'
        return redirect('catalog')

    try:
        book = Book.objects.get(isbn=isbn)
        wl, created = WaitList.objects.get_or_create(client=client, book=book)
        if created:
            log_action(user_id, 'INSERT', 'waitlist', f'Добавлена в лист ожидания: {book.title}', wl.id)
            request.session['flash_msg'] = f'Книга «{book.title}» добавлена в лист ожидания.'
        else:
            request.session['flash_msg'] = 'Вы уже ожидаете эту книгу.'
    except Exception as e:
        request.session['flash_err'] = f'Ошибка: {e}'

    return redirect('catalog')

# 4. УПРАВЛЕНИЕ СОТРУДНИКАМИ / ПОЛЬЗОВАТЕЛЯМИ (Только Admin)
def staff_view(request):
    if request.session.get('role') != 'Admin':
        return redirect('login')

    admin_id = request.session.get('user_id')
    message = None
    error = None

    if request.method == 'POST':
        act = request.POST.get('act')
        try:
            if act == 'add':
                uname = request.POST.get('username', '').strip()
                pwd = request.POST.get('password_hash', '').strip()
                role = request.POST.get('role', 'Manager')
                new_u = SystemUser.objects.create(username=uname, password_hash=pwd, role=role)
                log_action(admin_id, 'INSERT', 'system_user', f'Добавлен пользователь {uname} ({role})', new_u.id)
                message = f'Пользователь «{uname}» добавлен.'
            elif act == 'update':
                uid = int(request.POST.get('user_id'))
                uname = request.POST.get('username', '').strip()
                pwd = request.POST.get('password_hash', '').strip()
                role = request.POST.get('role')
                u = SystemUser.objects.get(id=uid)
                u.username = uname
                u.password_hash = pwd
                u.role = role
                u.save()
                log_action(admin_id, 'UPDATE', 'system_user', f'Обновлён пользователь ID={uid} ({uname}, {role})', uid)
                message = f'Данные пользователя ID={uid} обновлены.'
            elif act == 'delete':
                uid = int(request.POST.get('user_id'))
                if uid == admin_id:
                    error = 'Нельзя удалить свою текущую учётную запись.'
                else:
                    u = SystemUser.objects.get(id=uid)
                    uname = u.username
                    u.delete()
                    log_action(admin_id, 'DELETE', 'system_user', f'Удалён пользователь ID={uid} ({uname})', uid)
                    message = f'Пользователь «{uname}» удалён.'
        except (IntegrityError, DatabaseError, ValueError) as e:
            error = f'Ошибка операции: {e}'

    filter_mode = request.GET.get('show', 'staff')
    if filter_mode == 'all':
        users = SystemUser.objects.all().order_by('id')[:100]
    else:
        users = SystemUser.objects.filter(role__in=['Admin', 'Manager']).order_by('id')

    return render(request, 'store/staff.html', {
        'users': users,
        'filter_mode': filter_mode,
        'message': message,
        'error': error
    })

# 5. ПОДТВЕРЖДЕНИЕ ЗАКАЗОВ (Admin и Manager, использует индекс idx_order_status и VIEW v_client_orders)
def orders_view(request):
    if request.session.get('role') not in ['Admin', 'Manager']:
        return redirect('login')

    staff_id = request.session.get('user_id')
    message = None
    error = None

    if request.method == 'POST':
        order_id = int(request.POST.get('order_id'))
        new_status = request.POST.get('new_status')
        try:
            order = Order.objects.get(id=order_id)
            old_status = order.status
            order.status = new_status
            order.updated_at = timezone.now()
            order.save()
            log_action(staff_id, 'UPDATE', 'order', f'Статус заказа #{order_id} изменён: {old_status} -> {new_status}', order_id)
            message = f'Заказ #{order_id} переведён в статус «{new_status}».'
        except Exception as e:
            error = f'Ошибка обновления заказа: {e}'

    status_filter = request.GET.get('status', 'processing')
    orders_qs = Order.objects.select_related('client', 'city').order_by('-id')
    if status_filter != 'all':
        # Этот запрос задействует созданный индекс idx_order_status
        orders_qs = orders_qs.filter(status=status_filter)

    orders_list = []
    for o in orders_qs[:50]:
        items = OrderBook.objects.filter(order=o).select_related('book')
        orders_list.append({
            'order': o,
            'items': items
        })

    return render(request, 'store/orders.html', {
        'orders_list': orders_list,
        'status_filter': status_filter,
        'message': message,
        'error': error
    })

# Отдача обложки прямо из PostgreSQL (BYTEA)
def book_cover_view(request, isbn):
    book = Book.objects.filter(isbn=isbn).first()
    if book and book.cover:
        return HttpResponse(bytes(book.cover), content_type='image/jpeg')
    return redirect('/static/img/default_cover.svg')


def lookup_isbn_view(request):
    if request.session.get('role') not in ['Admin', 'Manager']:
        return JsonResponse({'status': 'forbidden'}, status=403)

    raw_query = request.GET.get('isbn', '').strip()
    if not raw_query:
        return JsonResponse({'status': 'invalid'})

    headers = {'User-Agent': 'BookStoreStudentApp/1.0 (student@bookstore.local)'}
    clean_digits = re.sub(r'[^0-9Xx]', '', raw_query)
    is_isbn = bool(re.fullmatch(r'\d{10}|\d{13}|\d{9}[Xx]', clean_digits))

    # --- РЕЖИМ 1: Введён ISBN ---
    if is_isbn:
        isbn_db = re.sub(r'\D', '', clean_digits)[:13]

        # 1. Проверяем свою базу данных
        existing = Book.objects.filter(isbn=isbn_db).first()
        if existing:
            return JsonResponse({
                'status': 'exists',
                'isbn': existing.isbn,
                'title': existing.title,
                'price': str(existing.price),
                'quantity': existing.quantity,
            })

        network_err = None

        # 2. Пробуем современный Open Library Search API по ISBN
        try:
            ol_search_url = (
                f'https://openlibrary.org/search.json?isbn={urllib.parse.quote(clean_digits)}'
                '&fields=title,author_name,publisher,first_publish_year,number_of_pages_median,cover_i,isbn&limit=1'
            )
            req = urllib.request.Request(ol_search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            docs = data.get('docs', [])
            if docs:
                doc = docs[0]
                cover_id = doc.get('cover_i')
                cover_url = f'https://covers.openlibrary.org/b/id/{cover_id}-M.jpg' if cover_id else ''
                pubs = doc.get('publisher') or []
                return JsonResponse({
                    'status': 'found_api',
                    'isbn': isbn_db,
                    'title': (doc.get('title') or '')[:150],
                    'authors': ', '.join(doc.get('author_name', [])[:3]),
                    'imprint': (pubs[0] if pubs else '')[:50],
                    'pages': doc.get('number_of_pages_median') or '',
                    'year': doc.get('first_publish_year') or '',
                    'summary': '',
                    'cover_url': cover_url,
                })
        except urllib.error.URLError as e:
            network_err = str(e.reason)
        except Exception as e:
            network_err = str(e)

        # 3. Запасной вариант: Open Library Books API
        try:
            ol_legacy_url = f'https://openlibrary.org/api/books?bibkeys=ISBN:{clean_digits}&format=json&jscmd=data'
            req = urllib.request.Request(ol_legacy_url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            key = f'ISBN:{clean_digits}'
            if key in data:
                info = data[key]
                authors = ', '.join(a.get('name', '') for a in info.get('authors', []) if a.get('name'))
                publishers = info.get('publishers', [])
                imprint_name = publishers[0].get('name', '') if publishers else ''
                year_match = re.search(r'\b(1[89]\d{2}|20\d{2})\b', str(info.get('publish_date', '')))
                cover_info = info.get('cover', {})
                return JsonResponse({
                    'status': 'found_api',
                    'isbn': isbn_db,
                    'title': info.get('title', '')[:150],
                    'authors': authors,
                    'imprint': imprint_name[:50],
                    'pages': info.get('number_of_pages') or '',
                    'year': int(year_match.group(1)) if year_match else '',
                    'summary': '',
                    'cover_url': cover_info.get('medium') or cover_info.get('large') or '',
                })
        except Exception:
            pass

        # 4. Запасной вариант: Google Books API (хорошо знает русские ISBN 9785...)
        try:
            gb_url = f'https://www.googleapis.com/books/v1/volumes?q=isbn:{clean_digits}'
            req = urllib.request.Request(gb_url, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as resp:
                gb_data = json.loads(resp.read().decode('utf-8'))

            items = gb_data.get('items', [])
            if items:
                vi = items[0].get('volumeInfo', {})
                year_match = re.search(r'\b(1[89]\d{2}|20\d{2})\b', str(vi.get('publishedDate', '')))
                img_links = vi.get('imageLinks', {})
                cover_url = (img_links.get('thumbnail') or '').replace('http://', 'https://')
                return JsonResponse({
                    'status': 'found_api',
                    'isbn': isbn_db,
                    'title': vi.get('title', '')[:150],
                    'authors': ', '.join(vi.get('authors', [])[:3]),
                    'imprint': vi.get('publisher', '')[:50],
                    'pages': vi.get('pageCount') or '',
                    'year': int(year_match.group(1)) if year_match else '',
                    'summary': vi.get('description', '')[:500],
                    'cover_url': cover_url,
                })
        except Exception:
            pass

        if network_err:
            return JsonResponse({'status': 'error', 'isbn': isbn_db, 'error': network_err})
        return JsonResponse({'status': 'not_found', 'isbn': isbn_db})

    # --- РЕЖИМ 2: Введено название книги (Global Book Search) ---
    try:
        q_encoded = urllib.parse.quote(raw_query)
        ol_title_url = (
            f'https://openlibrary.org/search.json?q={q_encoded}'
            '&fields=title,author_name,publisher,first_publish_year,number_of_pages_median,cover_i,isbn&limit=5'
        )
        req = urllib.request.Request(ol_title_url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        results = []
        for doc in data.get('docs', []):
            isbns = doc.get('isbn', [])
            best_isbn = next((x for x in isbns if x.isdigit() and len(x) == 13), '')
            if not best_isbn:
                best_isbn = next((re.sub(r'\D', '', x)[:13] for x in isbns if re.sub(r'\D', '', x)), '')
            cover_id = doc.get('cover_i')
            pubs = doc.get('publisher') or []
            results.append({
                'isbn': best_isbn,
                'title': (doc.get('title') or '')[:150],
                'authors': ', '.join(doc.get('author_name', [])[:3]),
                'imprint': (pubs[0] if pubs else '')[:50],
                'year': doc.get('first_publish_year') or '',
                'pages': doc.get('number_of_pages_median') or '',
                'cover_url': f'https://covers.openlibrary.org/b/id/{cover_id}-M.jpg' if cover_id else '',
            })

        if results:
            return JsonResponse({'status': 'search_results', 'results': results})
        return JsonResponse({'status': 'not_found', 'isbn': ''})
    except Exception as e:
        return JsonResponse({'status': 'error', 'isbn': '', 'error': str(e)})


# УПРАВЛЕНИЕ КНИГАМИ (как в реальном магазине: приёмка по ISBN, несколько авторов/жанров, обложка)
def books_manage_view(request):
    if request.session.get('role') not in ['Admin', 'Manager']:
        return redirect('login')

    staff_id = request.session.get('user_id')
    message = None
    error = None

    if request.method == 'POST':
        act = request.POST.get('act')
        try:
            if act == 'restock':
                isbn = re.sub(r'\D', '', request.POST.get('isbn', ''))[:13]
                add_qty = int(request.POST.get('add_quantity', 0))
                new_price = float(request.POST.get('price'))
                book = Book.objects.get(isbn=isbn)
                book.quantity += max(0, add_qty)
                book.price = new_price
                book.save()
                log_action(staff_id, 'UPDATE', 'book', f'Оприходована поставка: +{add_qty} шт. «{book.title}» [ISBN:{isbn}]')
                message = f'Поставка принята! Для «{book.title}» теперь на складе {book.quantity} шт.'

            elif act == 'add':
                isbn = re.sub(r'\D', '', request.POST.get('isbn', ''))[:13]
                title = request.POST.get('title', '').strip()[:150]
                price = float(request.POST.get('price'))
                qty = int(request.POST.get('quantity'))
                lang_id = int(request.POST.get('language_id'))
                imprint_name = request.POST.get('imprint_name', '').strip()[:50] or 'Неизвестное издательство'
                authors_text = request.POST.get('authors_text', '').strip()
                genre_ids = request.POST.getlist('genre_ids')
                summary = request.POST.get('summary', '').strip()[:500] or None

                pages_raw = request.POST.get('page_count', '').strip()
                page_count = int(pages_raw) if pages_raw else None

                year_raw = request.POST.get('publication_year', '').strip()
                pub_year = int(year_raw) if year_raw else None

                if Book.objects.filter(isbn=isbn).exists():
                    error = f'Книга с ISBN {isbn} уже есть в базе! Используйте поиск по ISBN, чтобы принять поставку.'
                else:
                    # Ищем или создаём издательство без учёта регистра
                    imprint_obj = Imprint.objects.filter(name__iexact=imprint_name).first()
                    if not imprint_obj:
                        imprint_obj = Imprint.objects.create(name=imprint_name)

                    # Читаем обложку (загруженный файл или скачанная по URL из Open Library)
                    cover_bytes = None
                    if request.FILES.get('cover_file'):
                        cover_bytes = request.FILES['cover_file'].read()
                    elif request.POST.get('cover_url'):
                        try:
                            req = urllib.request.Request(
                                request.POST['cover_url'],
                                headers={'User-Agent': 'BookStoreStudentApp/1.0'}
                            )
                            with urllib.request.urlopen(req, timeout=3) as resp:
                                cover_bytes = resp.read()
                        except Exception:
                            cover_bytes = None

                    book = Book.objects.create(
                        isbn=isbn,
                        title=title,
                        price=price,
                        quantity=qty,
                        language_id=lang_id,
                        imprint=imprint_obj,
                        summary=summary,
                        page_count=page_count,
                        publication_year=pub_year,
                        cover=cover_bytes
                    )

                    # Привязываем одного или нескольких авторов (создаём, если автора ещё нет в БД)
                    author_names = [a.strip() for a in authors_text.split(',') if a.strip()]
                    if not author_names:
                        author_names = ['Автор Неуказан']

                    for full_name in author_names:
                        parts = full_name.split()
                        fn = parts[0][:30]
                        ln = (' '.join(parts[1:]) if len(parts) > 1 else '—')[:30]
                        author_obj = Author.objects.filter(first_name__iexact=fn, last_name__iexact=ln).first()
                        if not author_obj:
                            author_obj = Author.objects.create(first_name=fn, last_name=ln)
                        BookAuthor.objects.get_or_create(book=book, author=author_obj)

                    # Привязываем выбранные жанры (M:N)
                    for gid in genre_ids:
                        BookGenre.objects.get_or_create(book=book, genre_id=int(gid))

                    log_action(staff_id, 'INSERT', 'book', f'Добавлена книга «{title}» [ISBN:{isbn}]')
                    message = f'Книга «{title}» успешно добавлена в каталог!'

            elif act == 'update':
                isbn = request.POST.get('isbn')
                price = float(request.POST.get('price'))
                qty = int(request.POST.get('quantity'))
                book = Book.objects.get(isbn=isbn)
                book.price = price
                book.quantity = qty
                book.save()
                log_action(staff_id, 'UPDATE', 'book', f'Обновлена книга «{book.title}» [ISBN:{isbn}]: цена {price}, ост. {qty}')
                message = f'Книга [ISBN:{isbn}] обновлена.'

            elif act == 'delete':
                isbn = request.POST.get('isbn')
                book = Book.objects.get(isbn=isbn)
                title = book.title
                book.delete()
                log_action(staff_id, 'DELETE', 'book', f'Удалена книга «{title}» [ISBN:{isbn}]')
                message = f'Книга «{title}» удалена.'
        except (IntegrityError, DatabaseError, ValueError) as e:
            error = f'Ошибка операции с книгой: {e}'

    # Поиск и пагинация в таблице управления книгами
    q = request.GET.get('q', '').strip()
    page_num = request.GET.get('page', 1)
    books_qs = Book.objects.select_related('imprint', 'language').order_by('-updated_at', 'isbn')
    if q:
        books_qs = books_qs.filter(title__icontains=q) | books_qs.filter(isbn__icontains=q)

    paginator = Paginator(books_qs, 20)
    page_obj = paginator.get_page(page_num)
    page_range = paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1)

    context = {
        'books': page_obj.object_list,
        'page_obj': page_obj,
        'page_range': page_range,
        'ellipsis': paginator.ELLIPSIS,
        'q': q,
        'authors': Author.objects.all().order_by('last_name')[:300],
        'genres': Genre.objects.all().order_by('name'),
        'languages': Language.objects.all().order_by('name'),
        'imprints': Imprint.objects.all().order_by('name'),
        'message': message,
        'error': error,
    }
    return render(request, 'store/books_manage.html', context)

# 7. АНАЛИТИКА: MATERIALIZED VIEW (Admin и Manager)
def analytics_view(request):
    if request.session.get('role') not in ['Admin', 'Manager']:
        return redirect('login')

    message = None
    if request.method == 'POST' and request.POST.get('act') == 'refresh':
        with connection.cursor() as cursor:
            cursor.execute('REFRESH MATERIALIZED VIEW mv_imprint_statistics;')
        log_action(request.session.get('user_id'), 'REFRESH', 'materialized_view', 'Обновлено представление mv_imprint_statistics')
        message = 'Материализованное представление успешно обновлено (REFRESH MATERIALIZED VIEW)!'

    with connection.cursor() as cursor:
        cursor.execute('SELECT imprint_id, "Издательство", "Количество книг", "Общая стоимость на складе", "Средняя цена" FROM mv_imprint_statistics ORDER BY "Общая стоимость на складе" DESC NULLS LAST;')
        stats = cursor.fetchall()

    return render(request, 'store/analytics.html', {'stats': stats, 'message': message})

def audit_view(request):
    if request.session.get('role') != 'Admin':
        return redirect('login')

    message = request.session.pop('flash_msg', None)
    error = request.session.pop('flash_err', None)

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT a.id, a.timestamp, COALESCE(u.username, 'Удалённый/Система'), a.action, a.object_type, a.object_id, a.details
            FROM audit_log a
            LEFT JOIN "system_user" u ON a.user_id = u.id
            ORDER BY a.timestamp DESC, a.id DESC
            LIMIT 60;
        """)
        logs = cursor.fetchall()

    return render(request, 'store/audit.html', {
        'logs': logs,
        'message': message,
        'error': error
    })

def undo_audit_view(request, log_id):
    if request.session.get('role') != 'Admin' or request.method != 'POST':
        return redirect('login')

    admin_id = request.session.get('user_id')
    try:
        log = AuditLog.objects.get(id=log_id)
        if '[ОТМЕНЕНО]' in (log.details or ''):
            request.session['flash_err'] = 'Это действие уже было отменено.'
            return redirect('audit')

        reverted_info = ''
        if log.object_type == 'book' and log.action == 'INSERT':
            match = re.search(r'ISBN:(\d+)', log.details or '')
            if match:
                isbn = match.group(1)
                Book.objects.filter(isbn=isbn).delete()
                reverted_info = f'Удалена ранее добавленная книга ISBN:{isbn}'
        elif log.object_type == 'order' and log.object_id:
            order = Order.objects.filter(id=log.object_id).first()
            if order:
                if log.action == 'INSERT':
                    order.status = 'cancelled'
                    order.save()
                    reverted_info = f'Отменён созданный заказ #{order.id}'
                elif log.action == 'UPDATE':
                    order.status = 'cancelled'
                    order.updated_at = timezone.now()
                    order.save()
                    reverted_info = f'Заказ #{order.id} отменён администратором'
        elif log.object_type == 'order_book' and log.action == 'INSERT' and log.object_id:
            OrderBook.objects.filter(id=log.object_id).delete()
            reverted_info = f'Удалена позиция #{log.object_id} из заказа'
        elif log.object_type == 'system_user' and log.action == 'INSERT' and log.object_id:
            if log.object_id != admin_id:
                SystemUser.objects.filter(id=log.object_id).delete()
                reverted_info = f'Удалён зарегистрированный пользователь ID={log.object_id}'
            else:
                request.session['flash_err'] = 'Нельзя отменить создание самого себя.'
                return redirect('audit')
        elif log.object_type == 'waitlist' and log.action == 'INSERT' and log.object_id:
            WaitList.objects.filter(id=log.object_id).delete()
            reverted_info = f'Удалена запись #{log.object_id} из листа ожидания'
        else:
            request.session['flash_err'] = 'Для данного типа операции автоматический откат не поддерживается.'
            return redirect('audit')

        if reverted_info:
            log.details = f'{log.details} [ОТМЕНЕНО]'
            log.save()
            log_action(admin_id, 'UNDO', log.object_type, f'Откат действия #{log.id}: {reverted_info}', log.object_id)
            request.session['flash_msg'] = f'Действие #{log.id} успешно отменено ({reverted_info}).'
        else:
            request.session['flash_err'] = 'Объект для отмены уже не существует в базе.'

    except Exception as e:
        request.session['flash_err'] = f'Ошибка при отмене действия: {e}'

    return redirect('audit')