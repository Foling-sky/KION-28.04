/**
 * Функции для автоматического обновления данных на страницах
 */

// Интервал обновления в миллисекундах (5 секунд)
const REFRESH_INTERVAL = 5000;

/**
 * Функция для получения данных с API
 * @param {string} url - URL API-эндпоинта
 * @returns {Promise<Object>} - Данные от API
 */
async function fetchData(url) {
    try {
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error('Ошибка при получении данных:', error);
        return null;
    }
}

/**
 * Функция для обновления статистики на странице
 * @param {Object} stats - Данные статистики
 */
function updateStatsPage(stats) {
    if (!stats) return;
    
    // Обновляем счетчики
    document.querySelector('.redis-keys-count').textContent = stats.redis.dedup_keys_count;
    document.querySelector('.postgres-events-count').textContent = stats.postgres.total_events;
    document.querySelector('.postgres-shards-count').textContent = stats.postgres.shard_count;
    
    // Обновляем распределение по шардам
    const shardsContainer = document.querySelector('.shards-distribution');
    if (shardsContainer) {
        shardsContainer.innerHTML = '';
        
        for (const [shardId, count] of Object.entries(stats.postgres.shards)) {
            const percentage = stats.postgres.total_events > 0 
                ? ((count / stats.postgres.total_events) * 100).toFixed(2) 
                : 0;
                
            const shardElement = document.createElement('div');
            shardElement.className = 'mb-3';
            shardElement.innerHTML = `
                <div class="d-flex justify-content-between align-items-center mb-1">
                    <h6 class="mb-0">Шард ${shardId.split('_')[1]}</h6>
                    <span class="badge bg-primary rounded-pill">
                        ${count} (${percentage}%)
                    </span>
                </div>
                <div class="progress" style="height: 10px;">
                    <div class="progress-bar" role="progressbar" 
                         style="width: ${percentage}%"
                         aria-valuenow="${percentage}" aria-valuemin="0" aria-valuemax="100"></div>
                </div>
            `;
            shardsContainer.appendChild(shardElement);
        }
    }
    
    // Обновляем время последнего обновления
    const timestampElement = document.querySelector('.last-update-time');
    if (timestampElement) {
        const date = new Date(stats.timestamp);
        timestampElement.textContent = date.toLocaleString();
    }
}

/**
 * Функция для обновления списка событий на странице
 * @param {Object} data - Данные событий
 * @param {number} limit - Количество событий на странице
 * @param {number} offset - Смещение для пагинации
 */
function updateEventsPage(data, limit, offset) {
    if (!data || !data.data) return;
    
    const events = data.data;
    const total = data.total;
    
    // Обновляем общее количество событий
    document.querySelector('.total-events-count').textContent = total;
    
    // Обновляем таблицу событий
    const tableBody = document.querySelector('.events-table tbody');
    if (tableBody) {
        tableBody.innerHTML = '';
        
        if (events.length === 0) {
            tableBody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center py-5">
                        <div class="d-flex flex-column align-items-center">
                            <i class="bi bi-inbox text-muted" style="font-size: 3rem;"></i>
                            <p class="mt-3 mb-0">Нет данных</p>
                        </div>
                    </td>
                </tr>
            `;
        } else {
            events.forEach(event => {
                const row = document.createElement('tr');
                row.className = 'event-row';
                row.onclick = () => window.location = `/api/ui/database?event_hash=${event.event_hash}`;
                
                row.innerHTML = `
                    <td><span class="fw-bold">${event.id}</span></td>
                    <td>${event.client_id}</td>
                    <td><span class="badge bg-info text-dark">${event.event_name}</span></td>
                    <td>${event.product_id}</td>
                    <td>${event.event_datetime}</td>
                    <td><span class="badge bg-secondary text-truncate" style="max-width: 120px;">${event.event_hash}</span></td>
                    <td>
                        <a href="/api/ui/database?event_hash=${event.event_hash}" class="btn btn-sm btn-primary">Подробнее</a>
                    </td>
                `;
                
                tableBody.appendChild(row);
            });
        }
    }
    
    // Обновляем мобильные карточки
    const mobileContainer = document.querySelector('.mobile-events');
    if (mobileContainer) {
        mobileContainer.innerHTML = '';
        
        if (events.length === 0) {
            mobileContainer.innerHTML = `
                <div class="card mb-3">
                    <div class="card-body text-center py-5">
                        <i class="bi bi-inbox text-muted" style="font-size: 3rem;"></i>
                        <p class="mt-3 mb-0">Нет данных</p>
                    </div>
                </div>
            `;
        } else {
            events.forEach(event => {
                const card = document.createElement('div');
                card.className = 'card mb-3 event-card';
                card.onclick = () => window.location = `/api/ui/database?event_hash=${event.event_hash}`;
                
                card.innerHTML = `
                    <div class="card-body">
                        <div class="d-flex justify-content-between align-items-center mb-2">
                            <h6 class="card-title mb-0">ID: ${event.id}</h6>
                            <span class="badge bg-success"><i class="bi bi-check-circle me-1"></i>Уникально</span>
                        </div>
                        <p class="card-text mb-1"><small class="text-muted">Событие:</small> <span class="badge bg-info text-dark">${event.event_name}</span></p>
                        <p class="card-text mb-1"><small class="text-muted">Клиент:</small> ${event.client_id}</p>
                        <p class="card-text mb-1"><small class="text-muted">Продукт:</small> ${event.product_id}</p>
                        <p class="card-text mb-1"><small class="text-muted">Дата:</small> ${event.event_datetime}</p>
                        <p class="card-text mb-0"><small class="text-muted">Хеш:</small> <span class="badge bg-secondary">${event.event_hash.substring(0, 8)}...</span></p>
                    </div>
                `;
                
                mobileContainer.appendChild(card);
            });
        }
    }
    
    // Обновляем пагинацию
    updatePagination(total, limit, offset);
}

/**
 * Функция для обновления пагинации
 * @param {number} total - Общее количество событий
 * @param {number} limit - Количество событий на странице
 * @param {number} offset - Текущее смещение
 */
function updatePagination(total, limit, offset) {
    const paginationContainer = document.querySelector('.pagination');
    if (!paginationContainer) return;
    
    const pages = Math.ceil(total / limit);
    const currentPage = Math.floor(offset / limit) + 1;
    
    let paginationHTML = '';
    
    // Кнопка "В начало"
    paginationHTML += `
        <li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
            <a class="page-link" href="/api/ui/database?limit=${limit}&offset=0" aria-label="В начало">
                <span aria-hidden="true">&laquo;&laquo;</span>
            </a>
        </li>
    `;
    
    // Кнопка "Предыдущая"
    paginationHTML += `
        <li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
            <a class="page-link" href="/api/ui/database?limit=${limit}&offset=${(currentPage - 2) * limit}" aria-label="Предыдущая">
                <span aria-hidden="true">&laquo;</span>
            </a>
        </li>
    `;
    
    // Номера страниц
    const startPage = Math.max(1, currentPage - 2);
    const endPage = Math.min(pages, currentPage + 2);
    
    for (let i = startPage; i <= endPage; i++) {
        paginationHTML += `
            <li class="page-item ${i === currentPage ? 'active' : ''}">
                <a class="page-link" href="/api/ui/database?limit=${limit}&offset=${(i - 1) * limit}">${i}</a>
            </li>
        `;
    }
    
    // Кнопка "Следующая"
    paginationHTML += `
        <li class="page-item ${currentPage === pages ? 'disabled' : ''}">
            <a class="page-link" href="/api/ui/database?limit=${limit}&offset=${currentPage * limit}" aria-label="Следующая">
                <span aria-hidden="true">&raquo;</span>
            </a>
        </li>
    `;
    
    // Кнопка "В конец"
    paginationHTML += `
        <li class="page-item ${currentPage === pages ? 'disabled' : ''}">
            <a class="page-link" href="/api/ui/database?limit=${limit}&offset=${(pages - 1) * limit}" aria-label="В конец">
                <span aria-hidden="true">&raquo;&raquo;</span>
            </a>
        </li>
    `;
    
    paginationContainer.innerHTML = paginationHTML;
}

/**
 * Функция для запуска автоматического обновления страницы статистики
 */
function startStatsAutoRefresh() {
    // Добавляем классы для обновления
    document.querySelector('.redis-keys-count').classList.add('redis-keys-count');
    document.querySelector('.postgres-events-count').classList.add('postgres-events-count');
    document.querySelector('.postgres-shards-count').classList.add('postgres-shards-count');
    document.querySelector('.shards-distribution').classList.add('shards-distribution');
    document.querySelector('.last-update-time').classList.add('last-update-time');
    
    // Функция обновления
    async function refreshStats() {
        const stats = await fetchData('/api/events/stats');
        if (stats) {
            updateStatsPage(stats);
        }
    }
    
    // Запускаем обновление сразу и затем по интервалу
    refreshStats();
    setInterval(refreshStats, REFRESH_INTERVAL);
}

/**
 * Функция для запуска автоматического обновления страницы событий
 * @param {number} limit - Количество событий на странице
 * @param {number} offset - Смещение для пагинации
 */
function startEventsAutoRefresh(limit, offset) {
    // Добавляем классы для обновления
    document.querySelector('.total-events-count').classList.add('total-events-count');
    document.querySelector('.events-table tbody').classList.add('events-table');
    document.querySelector('.mobile-events').classList.add('mobile-events');
    
    // Функция обновления
    async function refreshEvents() {
        const data = await fetchData(`/api/events/events?limit=${limit}&offset=${offset}`);
        if (data) {
            updateEventsPage(data, limit, offset);
        }
    }
    
    // Запускаем обновление сразу и затем по интервалу
    refreshEvents();
    setInterval(refreshEvents, REFRESH_INTERVAL);
} 