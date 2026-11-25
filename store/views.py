from django.shortcuts import render, redirect
from django.db import connection
from .models import SystemUser, Book, AuditLog

# 1. Авторизация (Sign In в стиле американских сайтов с выпадающим выбором аккаунта или вводом)
def login_view(request):
    error = None
    if request.method == 'POST':
        username_input = request.POST.get('username')
        password_input = request.POST.get('password')
        
        try:
            # Ищем пользователя в базе
            user = SystemUser.objects.get(username=username_input, password_hash=password_input)
            
            # Сохраняем в сессию
            request.session['user_id'] = user.id
            request.session['username'] = user.username
            request.session['role'] = user.role
            
            # ЖУРНАЛИРОВАНИЕ АВТОРИЗАЦИИ (Лаба 6-7)
            AuditLog.objects.create(
                user_id=user.id,
                action='LOGIN',
                object_type='system_user',
                details=f'Пользователь {user.username} успешно вошел в систему'
            )
            
            if user.role in ['Admin', 'Manager']:
                return redirect('admin_panel')
            else:
                return redirect('catalog')
                
        except SystemUser.DoesNotExist:
            error = 'Неверный логин или пароль'
            
    return render(request, 'store/login.html', {'error': error})


# 2. Каталог книг (Использует наше VIEW и Индекс для поиска)
def catalog_view(request):
    query = request.GET.get('q', '')
    
    # Используем чистый SQL для обращения к нашему материализованному или обычному VIEW,
    # либо используем индекс через поиск в PostgreSQL
    with connection.cursor() as cursor:
        if query:
            # Поиск использует созданный индекс idx_book_title
            cursor.execute(
                """SELECT isbn, "Название", "Автор(ы)", "Издательство", "Язык", "Цена", "Остаток" 
                   FROM v_book_catalog 
                   WHERE "Название" ILIKE %s""",
                [f'%{query}%']
            )
        else:
            cursor.execute('SELECT isbn, "Название", "Автор(ы)", "Издательство", "Язык", "Цена", "Остаток" FROM v_book_catalog LIMIT 20')
        
        books = cursor.fetchall()
        
    return render(request, 'store/catalog.html', {'books': books, 'query': query})


# 3. Панель Администратора / Управление сотрудниками (Стиль Shopify / Amazon Admin)
def admin_panel_view(request):
    # Проверка прав доступа (Разграничение доступа)
    if request.session.get('role') not in ['Admin', 'Manager']:
        return redirect('login')
        
    # Получаем список сотрудников (как на твоем макете "Our Team")
    managers = SystemUser.objects.filter(role__in=['Admin', 'Manager'])
    
    return render(request, 'store/admin_panel.html', {'managers': managers})


# 4. Просмотр журнала действий (Audit Log)
def audit_log_view(request):
    if request.session.get('role') != 'Admin':
        return redirect('login')
        
    # Вывод журнала с именами пользователей через JOIN
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT a.timestamp, u.username, a.action, a.object_type, a.details 
            FROM audit_log a
            JOIN system_user u ON a.user_id = u.id
            ORDER BY a.timestamp DESC
            LIMIT 50
        """)
        logs = cursor.fetchall()
        
    return render(request, 'store/audit_log.html', {'logs': logs})