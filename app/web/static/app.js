const state = {
  token: null,
  authenticated: false,
  telegram: null,
  isAdmin: false,
  dashboard: null,
  control: null,
  performance: null,
  operations: null,
  referrals: null,
  systemRuntime: null,
  billing: null,
  admin: null,
  adminSelectedUser: null,
  demo: null,
  demoLoading: false,
};

const $ = (id) => document.getElementById(id);

const elements = {
  statusBanner: $('statusBanner'),
  webLoginScreen: $('webLoginScreen'),
  webLoginMessage: $('webLoginMessage'),
  logoutButton: $('logoutButton'),
  refreshButton: $('refreshButton'),
  refreshButtonTop: $('refreshButtonTop'),
  sidebarUser: $('sidebarUser'),
  sidebarPlan: $('sidebarPlan'),
  connectionBadge: $('connectionBadge'),
  adminTabButton: $('adminTabButton'),
  demoNotice: $('demoNotice'),
  demoStatusBadge: $('demoStatusBadge'),
  demoEquity: $('demoEquity'),
  demoPnl: $('demoPnl'),
  demoStats: $('demoStats'),
  demoPosition: $('demoPosition'),
  demoHistory: $('demoHistory'),
  demoHistoryBadge: $('demoHistoryBadge'),
  demoStartButton: $('demoStartButton'),
  demoPauseButton: $('demoPauseButton'),
  demoResetButton: $('demoResetButton'),
  demoCapitalForm: $('demoCapitalForm'),
  demoCapitalInput: $('demoCapitalInput'),
  demoCapitalSave: $('demoCapitalSave'),
  demoCapitalHint: $('demoCapitalHint'),
  systemTabButton: $('systemTabButton'),
  heroSessionPill: $('heroSessionPill'),
  heroPlanPill: $('heroPlanPill'),
  heroTradingPill: $('heroTradingPill'),
  heroUser: $('heroUser'),
  heroUserSubtext: $('heroUserSubtext'),
  heroWallet: $('heroWallet'),
  heroWalletSubtext: $('heroWalletSubtext'),
  heroKey: $('heroKey'),
  heroKeySubtext: $('heroKeySubtext'),
  heroAccess: $('heroAccess'),
  heroAccessSubtext: $('heroAccessSubtext'),
  userPlanBadge: $('userPlanBadge'),
  userReadinessBadge: $('userReadinessBadge'),
  walletBadge: $('walletBadge'),
  userIdentity: $('userIdentity'),
  userIdentitySubtext: $('userIdentitySubtext'),
  userReadiness: $('userReadiness'),
  userTradingStatus: $('userTradingStatus'),
  walletConfigured: $('walletConfigured'),
  privateKeyConfigured: $('privateKeyConfigured'),
  dashboardStats: $('dashboardStats'),
  readinessList: $('readinessList'),
  performanceGrid: $('performanceGrid'),
  performanceExecutiveGrid: $('performanceExecutiveGrid'),
  referralStats: $('referralStats'),
  accessStatsGrid: $('accessStatsGrid'),
  userActivityList: $('userActivityList'),
  summarySystemStatusPanel: $('summarySystemStatusPanel'),
  summarySystemOverviewPanel: $('summarySystemOverviewPanel'),
  systemHealthGrid: $('systemHealthGrid'),
  systemRuntimeGrid: $('systemRuntimeGrid'),
  systemRuntimeNotes: $('systemRuntimeNotes'),
  systemHealthGridPanel: $('systemHealthGridPanel'),
  backendHealthCard: $('backendHealthCard'),
  systemRuntimeGridPanel: $('systemRuntimeGridPanel'),
  systemActivityList: $('systemActivityList'),
  controlReadinessBox: $('controlReadinessBox'),
  controlStats: $('controlStats'),
  termsStatusText: $('termsStatusText'),
  walletInput: $('walletInput'),
  privateKeyInput: $('privateKeyInput'),
  configurationForm: $('configurationForm'),
  saveConfigurationButton: $('saveConfigurationButton'),
  clearConfigurationButton: $('clearConfigurationButton'),
  acceptTermsButton: $('acceptTermsButton'),
  activateTradingButton: $('activateTradingButton'),
  pauseTradingButton: $('pauseTradingButton'),
  billingCatalogGrid: $('billingCatalogGrid'),
  billingOrderBox: $('billingOrderBox'),
  billingConfirmButton: $('billingConfirmButton'),
  billingCancelButton: $('billingCancelButton'),
  billingConfigGrid: $('billingConfigGrid'),
  billingHelpList: $('billingHelpList'),
  activeTradeBadge: $('activeTradeBadge'),
  activeTradeSummary: $('activeTradeSummary'),
  latestOpenSummary: $('latestOpenSummary'),
  latestCloseSummary: $('latestCloseSummary'),
  operationsSummaryGrid: $('operationsSummaryGrid'),
  timelineCount: $('timelineCount'),
  operationsTimelineGrid: $('operationsTimelineGrid'),
  operationsTimelineList: $('operationsTimelineList'),
  operationsCount: $('operationsCount'),
  operationsList: $('operationsList'),
  adminVisualStats: $('adminVisualStats'),
  adminSecurityStats: $('adminSecurityStats'),
  adminTradeStats: $('adminTradeStats'),
  adminRecentActions: $('adminRecentActions'),
  adminMonitorStats: $('adminMonitorStats'),
  adminMonitorFeed: $('adminMonitorFeed'),
  adminComponentTelemetry: $('adminComponentTelemetry'),
  adminOperatorSnapshots: $('adminOperatorSnapshots'),
  adminClearMonitorButton: $('adminClearMonitorButton'),
  adminClearMonitorStatus: $('adminClearMonitorStatus'),
  adminLivePositions: $('adminLivePositions'),
  adminSearchForm: $('adminSearchForm'),
  adminSearchInput: $('adminSearchInput'),
  adminSearchButton: $('adminSearchButton'),
  adminSearchResults: $('adminSearchResults'),
  adminUserDetail: $('adminUserDetail'),
  adminUserTitle: $('adminUserTitle'),
  adminUserSubtitle: $('adminUserSubtitle'),
  adminPlanSelect: $('adminPlanSelect'),
  adminPlanDaysInput: $('adminPlanDaysInput'),
  adminGrantPlanButton: $('adminGrantPlanButton'),
  adminPlanGrantHint: $('adminPlanGrantHint'),
  adminPlanPreviewBox: $('adminPlanPreviewBox'),
  adminActionReasonInput: $('adminActionReasonInput'),
  adminActivateTradingButton: $('adminActivateTradingButton'),
  adminPauseTradingButton: $('adminPauseTradingButton'),
  adminCloseTradingButton: $('adminCloseTradingButton'),
  adminMigrateKeyButton: $('adminMigrateKeyButton'),
  adminResetCredentialsButton: $('adminResetCredentialsButton'),
  adminResetStatsButton: $('adminResetStatsButton'),
  adminBulkMigrateButton: $('adminBulkMigrateButton'),
  adminBulkMigrationStatus: $('adminBulkMigrationStatus'),
  adminUserStats: $('adminUserStats'),
  adminUserPerformance: $('adminUserPerformance'),
  adminUserActionHistory: $('adminUserActionHistory'),
};

function setStatus(message, variant = 'info') {
  elements.statusBanner.className = `status-banner ${variant}`;
  elements.statusBanner.textContent = message;
}

function pillClass(value) {
  const normalized = String(value || '').toLowerCase();
  if (['ready', 'active', 'premium', 'configured', 'connected', 'ok', 'sí', 'yes', 'true', 'activo'].includes(normalized)) return 'success';
  if (['trial', 'warning', 'partial', 'preview', 'pendiente'].includes(normalized)) return 'warning';
  if (['inactive', 'false', 'not_ready', 'none', 'missing', 'error', 'offline', 'no', 'blocked', 'bloqueado'].includes(normalized)) return 'danger';
  if (['info', 'loading'].includes(normalized)) return 'info';
  return 'neutral';
}

function setPill(element, label, valueForClass = label) {
  if (!element) return;
  element.textContent = label;
  element.className = `status-pill ${pillClass(valueForClass)}`;
}

function setSummarySystemVisibility() {
  if (elements.summarySystemStatusPanel) elements.summarySystemStatusPanel.classList.add('hidden');
  if (elements.summarySystemOverviewPanel) elements.summarySystemOverviewPanel.classList.add('hidden');
}

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function planLabel(plan) {
  const normalized = String(plan || '').toLowerCase();
  if (normalized === 'trial') return 'PRUEBA';
  if (normalized === 'premium') return 'PREMIUM';
  if (!normalized || normalized === 'none') return 'SIN PLAN';
  return normalized.toUpperCase();
}

function accessLabel(data) {
  return data.access_label || (data.plan_active ? 'Activo' : 'Sin acceso');
}

function accessDetail(data) {
  if (data.access_detail) return data.access_detail;
  if (data.plan_active && data.plan_days_remaining > 0) return `${data.plan_days_remaining} día(s) restantes`;
  return data.plan_active ? 'Acceso vigente' : 'Sin acceso vigente';
}

function renderMultilineText(value, emptyCopy = '—') {
  const raw = String(value ?? '').replace(/\\n/g, '\n').trim();
  const normalized = raw || emptyCopy;
  return escapeHtml(normalized).replace(/\n/g, '<br>');
}

function renderSummaryMetrics(metrics = []) {
  const rows = Array.isArray(metrics) ? metrics.filter((item) => item && item.label) : [];
  if (!rows.length) return '';
  return `
    <div class="list-item-grid summary-metrics-grid">
      ${rows.map((item) => `<div><span class="metric-label">${escapeHtml(item.label)}</span><strong>${escapeHtml(String(item.value ?? '—'))}</strong></div>`).join('')}
    </div>
  `;
}

function renderEventSummary(container, title, summary, emptyCopy) {
  if (!container) return;
  if (!summary || (!summary.title && !summary.detail)) {
    container.className = 'list-stack empty-state';
    container.textContent = emptyCopy;
    return;
  }
  container.className = 'event-summary';
  container.innerHTML = '';
  const item = document.createElement('article');
  item.className = 'list-item';
  item.innerHTML = `
    <div class="list-item-title">${escapeHtml(summary.title || title)}</div>
    <div class="list-item-meta">${renderMultilineText(summary.detail || emptyCopy, emptyCopy)}</div>
    ${renderSummaryMetrics(summary.metrics || [])}
  `;
  container.appendChild(item);
}


function renderLiveTradeSummary(container, summary, emptyCopy) {
  if (!container) return;
  if (!summary) {
    container.className = 'list-stack empty-state';
    container.textContent = emptyCopy;
    return;
  }
  container.className = 'event-summary';
  container.innerHTML = '';
  const item = document.createElement('article');
  item.className = 'list-item';
  item.innerHTML = `
    <div class="list-item-title">${escapeHtml(summary.title || 'Operación activa')}</div>
    <div class="list-item-meta">${renderMultilineText(summary.detail || emptyCopy, emptyCopy)}</div>
    ${renderSummaryMetrics(summary.metrics || [])}
    <div class="list-item-meta subtle">${summary.started_at ? `Abierta ${formatDate(summary.started_at)}` : 'Sin timestamp de apertura.'}</div>
  `;
  container.appendChild(item);
}

function formatBlockers(list) {
  const rows = Array.isArray(list) ? list.filter(Boolean) : [];
  return rows.length ? rows.map((item) => `• ${item}`).join('\n') : '• Sin bloqueadores';
}

function formatActivityDetail(item) {
  return item?.detail || 'Sin detalle adicional.';
}

function formatFreshness(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return '—';
  if (value < 60) return `${value}s`;
  if (value < 3600) return `${Math.floor(value / 60)}m`;
  return `${Math.floor(value / 3600)}h`;
}

function runtimeStatusLabel(status) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'healthy') return 'Sano';
  if (normalized === 'warning' || normalized === 'stale') return 'Atención';
  if (normalized === 'degraded') return 'Degradado';
  if (normalized === 'online') return 'Online';
  if (normalized === 'offline') return 'Offline';
  if (normalized === 'error') return 'Error';
  return normalized.toUpperCase() || '—';
}

function buildSystemComponentCard(label, component = {}) {
  const status = component.status || 'offline';
  const freshness = component.freshness_seconds;
  const article = document.createElement('article');
  article.className = `kpi-card runtime-card ${pillClass(status)}`;
  article.innerHTML = `
    <div class="runtime-card-head">
      <span class="kpi-label">${label}</span>
      <span class="status-pill ${pillClass(status)}">${runtimeStatusLabel(status)}</span>
    </div>
    <div class="kpi-value">${component.last_seen_at ? formatDate(component.last_seen_at) : 'Sin registro reciente'}</div>
    <div class="kpi-subtext">${component.message || 'Sin actualización reciente'} · Actualización ${formatFreshness(freshness)}</div>
  `;
  return article;
}

function systemStatusCopy(status) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'healthy') return 'La plataforma opera con normalidad.';
  if (normalized === 'warning' || normalized === 'stale') return 'La plataforma sigue operativa, pero hay una atención temporal en curso.';
  if (normalized === 'degraded') return 'Hay una incidencia temporal en algunos servicios. Algunas funciones pueden tardar más de lo normal.';
  return 'La plataforma está sincronizando su estado operativo.';
}

function publicIssueSummary(status) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'healthy') return { label: 'Operativo', description: 'Todos los servicios principales responden correctamente.', tone: 'active' };
  if (normalized === 'warning' || normalized === 'stale') return { label: 'Atención', description: 'Se detectó una condición temporal que no bloquea el uso general.', tone: 'warning' };
  if (normalized === 'degraded') return { label: 'Incidencia', description: 'Hay una degradación temporal en la plataforma.', tone: 'blocked' };
  return { label: 'Sincronizando', description: 'La plataforma está resolviendo su estado actual.', tone: 'warning' };
}

function renderPublicSystemOverview(payload) {
  const publicSummary = payload.public_summary || {};
  const checkedAt = payload.checked_at || publicSummary.last_update_at;
  const summary = publicIssueSummary(payload.overall_status);
  const activityLabel = publicSummary.recent_activity_label || 'Sin actividad reciente';
  const activityDetail = publicSummary.recent_activity_detail || 'Todavía no hay actividad reciente para mostrar.';
  const syncLabel = publicSummary.connection_label || 'Sincronizada';
  const syncDetail = checkedAt ? `Última actualización ${formatDate(checkedAt)}` : 'Sin actualización reciente.';

  const healthCards = [
    buildKpiCard('Estado del sistema', runtimeStatusLabel(payload.overall_status), systemStatusCopy(payload.overall_status)),
    buildKpiCard('Conectividad', syncLabel, syncDetail),
    buildKpiCard('Actividad reciente', activityLabel, activityDetail),
  ];

  const overviewCards = [
    buildKpiCard('Operaciones activas', publicSummary.active_trades ?? 0, 'Posiciones activas registradas en el sistema.'),
    buildKpiCard('Estado de ejecución', publicSummary.execution_label || 'Monitoreado', publicSummary.execution_detail || 'Visión resumida del estado operativo.'),
    buildKpiCard('Última lectura', checkedAt ? formatDate(checkedAt) : '—', 'Hora de la última sincronización visual.'),
  ];

  if (elements.systemHealthGrid) {
    elements.systemHealthGrid.innerHTML = '';
    healthCards.forEach((card) => elements.systemHealthGrid.appendChild(card));
  }

  if (elements.systemRuntimeGrid) {
    elements.systemRuntimeGrid.innerHTML = '';
    overviewCards.forEach((card) => elements.systemRuntimeGrid.appendChild(card));
  }

  if (elements.accessStatsGrid) elements.accessStatsGrid.innerHTML = '';
  if (elements.performanceExecutiveGrid) elements.performanceExecutiveGrid.innerHTML = '';
  if (elements.systemRuntimeNotes) {
    elements.systemRuntimeNotes.innerHTML = '';
    elements.systemRuntimeNotes.className = 'readiness-list compact-list';
    elements.systemRuntimeNotes.appendChild(buildReadinessItem(summary.label, summary.description, summary.tone));
    if (publicSummary.plan_notice) {
      elements.systemRuntimeNotes.appendChild(buildReadinessItem('Acceso', publicSummary.plan_notice, 'active'));
    }
  }
}

function renderTechnicalSystemPanel(payload) {
  const components = payload.components || {};
  const runtime = payload.runtime || {};
  const cards = [
    buildSystemComponentCard('Backend web', { status: payload.backend?.status || 'online', last_seen_at: payload.backend?.checked_at, message: payload.backend?.message, freshness_seconds: 0 }),
    buildSystemComponentCard('Canal Telegram', components.telegram_bot || {}),
    buildSystemComponentCard('Motor DEMO', components.trading_loop || {}),
    buildSystemComponentCard('Scanner DEMO', components.scanner || {}),
  ];

  if (elements.systemHealthGridPanel) {
    elements.systemHealthGridPanel.innerHTML = '';
    cards.forEach((card) => elements.systemHealthGridPanel.appendChild(card.cloneNode(true)));
  }

  const runtimeCards = [
    buildKpiCard('Estado global', runtimeStatusLabel(payload.overall_status), payload.issues?.length ? `${payload.issues.length} incidencia(s) detectadas.` : 'Sin alertas críticas ahora mismo.'),
    buildKpiCard('Usuarios con plan', runtime.users_with_active_plan || 0, 'Acceso vigente en esta base.'),
    buildKpiCard('Trading activo', runtime.users_trading_active || 0, 'Usuarios con trading activo.'),
    buildKpiCard('Operaciones activas', runtime.active_trades || 0, 'Operaciones abiertas registradas.'),
    buildKpiCard('Última actividad del scanner', runtime.scanner_last_event || '—', runtime.scanner_last_symbol ? `Símbolo ${runtime.scanner_last_symbol}` : 'Sin símbolo reciente.'),
    buildKpiCard('Última actividad del manager', runtime.latest_trade_manager?.manager_heartbeat_at ? formatDate(runtime.latest_trade_manager.manager_heartbeat_at) : 'Sin registro', runtime.latest_trade_manager?.symbol ? `${runtime.latest_trade_manager.symbol} · user ${runtime.latest_trade_manager.user_id}` : 'Sin trade activo reciente.'),
  ];

  if (elements.systemRuntimeGridPanel) {
    elements.systemRuntimeGridPanel.innerHTML = '';
    runtimeCards.forEach((card) => elements.systemRuntimeGridPanel.appendChild(card));
  }

  if (elements.backendHealthCard) {
    elements.backendHealthCard.innerHTML = '';
    elements.backendHealthCard.append(
      buildKpiCard('Backend', 'ONLINE', payload.backend?.message || 'El backend respondió.'),
      buildKpiCard('Chequeado', payload.backend?.checked_at ? formatDate(payload.backend.checked_at) : '—', 'Timestamp de esta respuesta.')
    );
  }

  if (elements.systemActivityList) {
    elements.systemActivityList.className = 'list-stack';
    elements.systemActivityList.innerHTML = '';
    elements.systemActivityList.append(
      buildActivityItem('Última apertura', runtime.latest_open, 'No hay aperturas registradas todavía.'),
      buildActivityItem('Último cierre', runtime.latest_close, 'No hay cierres registrados todavía.'),
    );
  }
}

function buildActivityItem(title, item, emptyCopy) {
  const article = document.createElement('article');
  article.className = 'list-item';
  if (!item) {
    article.innerHTML = `<div class="list-item-title">${title}</div><div class="list-item-meta">${emptyCopy}</div>`;
    return article;
  }
  article.innerHTML = `
    <div class="list-item-header">
      <div>
        <div class="list-item-title">${title}</div>
        <div class="list-item-meta">${item.username ? '@' + item.username : 'ID ' + (item.user_id ?? '—')} · ${formatDate(item.at)}</div>
      </div>
      <span class="status-pill neutral">${item.symbol || '—'}</span>
    </div>
    <div class="list-item-meta">${renderMultilineText(item.event || 'Sin payload resumido.', 'Sin payload resumido.')}</div>
  `;
  return article;
}


function adminActionLabel(action) {
  const normalized = String(action || '').toLowerCase();
  const labels = {
    grant_manual_plan_days: 'Extensión manual de plan',
    activate_user_trading: 'Reanudar trading',
    pause_user_trading: 'Pausar trading',
    migrate_user_private_key: 'Migrar private key',
    reset_user_credentials: 'Resetear credencial',
    reset_user_stats: 'Resetear rendimiento',
    bulk_migrate_legacy_keys: 'Migración masiva de keys legacy',
    activate_premium_fixed_30d: 'Activación fija Premium 30d',
  };
  return labels[normalized] || normalized.replace(/_/g, ' ').toUpperCase();
}

function getAdminActionReason() {
  return elements.adminActionReasonInput ? elements.adminActionReasonInput.value.trim() : '';
}

function renderAdminActionHistory(container, items, emptyText) {
  if (!container) return;
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) {
    container.className = 'list-stack empty-state';
    container.textContent = emptyText;
    return;
  }

  container.className = 'admin-history-list';
  container.innerHTML = '';

  rows.forEach((row) => {
    const article = document.createElement('article');
    article.className = 'admin-history-item';
    const actor = row.actor_username ? `@${row.actor_username}` : (row.actor_user_id ? `ID ${row.actor_user_id}` : 'Admin');
    const target = row.target_username ? `@${row.target_username}` : (row.target_user_id ? `ID ${row.target_user_id}` : 'Global');
    const statusClass = row.status === 'rejected' ? 'danger' : 'info';
    const metadata = row.metadata || {};
    const extra = [];
    if (metadata.plan) extra.push(`Plan ${planLabel(metadata.plan)}`);
    if (metadata.days) extra.push(`${metadata.days} día(s)`);
    if (metadata.migrated_count) extra.push(`${metadata.migrated_count} migradas`);
    article.innerHTML = `
      <div class="admin-history-head">
        <div>
          <div class="admin-history-title">${escapeHtml(adminActionLabel(row.action))}</div>
          <div class="admin-history-meta">${escapeHtml(formatDate(row.created_at))} · ${escapeHtml(actor)} → ${escapeHtml(target)}${extra.length ? ` · ${escapeHtml(extra.join(' · '))}` : ''}</div>
        </div>
        <span class="status-pill ${statusClass}">${row.status === 'rejected' ? 'RECHAZADA' : 'REGISTRADA'}</span>
      </div>
      <div class="admin-history-body">
        <p class="admin-history-message"><strong>Resultado:</strong> ${escapeHtml(row.message || 'Sin mensaje adicional.')}</p>
        ${row.reason ? `<p class="admin-history-reason"><strong>Motivo:</strong> ${escapeHtml(row.reason)}</p>` : ''}
      </div>
    `;
    container.appendChild(article);
  });
}

async function requestStrongConfirmation(title, lines = []) {
  const promptText = `${title}

${lines.filter(Boolean).join('\n')}

Escribe CONFIRMAR para continuar.`;
  const value = window.prompt(promptText, '');
  if (value === null) {
    setStatus('Acción cancelada.', 'warning');
    return false;
  }
  if (value.trim().toUpperCase() !== 'CONFIRMAR') {
    setStatus('Confirmación inválida. Escribe exactamente CONFIRMAR.', 'warning');
    return false;
  }
  return true;
}

function pretty(value) {
  if (!value) return 'Sin datos';
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatNumber(value, digits = 2) {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric)) return '0';
  return numeric.toFixed(digits);
}

function truncateMiddle(value, max = 18) {
  const text = String(value || '');
  if (!text) return '—';
  if (text.length <= max) return text;
  const side = Math.floor((max - 3) / 2);
  return `${text.slice(0, side)}...${text.slice(-side)}`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function copyTextToClipboard(value, successMessage = 'Copiado correctamente.') {
  const text = String(value || '').trim();
  if (!text) {
    setStatus('No hay un valor disponible para copiar.', 'warning');
    return false;
  }

  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.setAttribute('readonly', 'readonly');
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      textarea.style.pointerEvents = 'none';
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(textarea);
      if (!ok) throw new Error('copy_failed');
    }
    setStatus(successMessage, 'success');
    return true;
  } catch (error) {
    setStatus('No se pudo copiar automáticamente. Intenta mantener pulsada la dirección.', 'error');
    return false;
  }
}

function shareReferralLink() {
  const data = state.referrals || {};
  const referralLink = String(data.referral_link || '').trim();
  if (!referralLink) {
    setStatus('Todavía no hay un enlace referido disponible.', 'warning');
    return;
  }
  const shareText = String(data.share_text || referralLink).trim();
  const telegramShareUrl = `https://t.me/share/url?url=${encodeURIComponent(referralLink)}&text=${encodeURIComponent(shareText)}`;
  try {
    if (state.telegram && typeof state.telegram.openTelegramLink === 'function') {
      state.telegram.openTelegramLink(telegramShareUrl);
    } else {
      window.open(telegramShareUrl, '_blank', 'noopener');
    }
    setStatus('Abriendo Telegram para compartir tu enlace referido.', 'info');
  } catch (error) {
    copyTextToClipboard(referralLink, 'Enlace referido copiado. Compártelo manualmente.');
  }
}

function buildCopyableAddress(address, label = 'Wallet receptora') {
  const full = String(address || '').trim();
  if (!full) {
    return `<div class="list-item-meta payment-order-detail">${label}: —</div>`;
  }

  const escaped = escapeHtml(full);
  const short = escapeHtml(truncateMiddle(full, 22));
  return `
    <div class="list-item-meta payment-order-detail payment-copy-block">
      <span class="payment-copy-label">${label}</span>
      <div class="payment-copy-row">
        <span class="payment-copy-value" title="${escaped}">${short}</span>
        <button class="inline-copy-button" type="button" data-copy-text="${escaped}">Copiar</button>
      </div>
    </div>
  `;
}

function buildKpiCard(label, value, subtext = '') {
  const article = document.createElement('article');
  article.className = 'kpi-card';
  article.innerHTML = `
    <span class="kpi-label">${label}</span>
    <div class="kpi-value">${value}</div>
    <div class="kpi-subtext">${subtext}</div>
  `;
  return article;
}

function buildReadinessItem(label, description, status) {
  const article = document.createElement('article');
  article.className = `readiness-item ${pillClass(status)}`;
  article.innerHTML = `
    <span class="readiness-dot"></span>
    <div class="readiness-copy">
      <strong>${label}</strong>
      <span>${description}</span>
    </div>
    <span class="status-pill ${pillClass(status)}">${status}</span>
  `;
  return article;
}

function buildPerformanceCard(windowLabel, stats) {
  const article = document.createElement('article');
  article.className = 'performance-card';
  article.innerHTML = `
    <span class="kpi-label">Ventana</span>
    <div class="performance-value">${windowLabel}</div>
    <div class="performance-subtext">PF ${stats.profit_factor === Infinity ? '∞' : formatNumber(stats.profit_factor, 2)} · Win ${formatNumber(stats.win_rate, 2)}%</div>
    <div class="performance-metrics">
      <div class="performance-mini"><span class="mini-label">Trades</span><strong>${stats.total}</strong></div>
      <div class="performance-mini"><span class="mini-label">PnL neto</span><strong>${formatNumber(stats.pnl_total, 4)}</strong></div>
      <div class="performance-mini"><span class="mini-label">Wins</span><strong>${stats.wins}</strong></div>
      <div class="performance-mini"><span class="mini-label">Losses</span><strong>${stats.losses}</strong></div>
    </div>
  `;
  return article;
}

function buildAdminPerformanceCard(windowLabel, stats = {}) {
  return buildPerformanceCard(windowLabel, {
    total: stats.total || 0,
    pnl_total: stats.pnl_total || 0,
    wins: stats.wins || 0,
    losses: stats.losses || 0,
    profit_factor: stats.profit_factor === Infinity ? Infinity : Number(stats.profit_factor || 0),
    win_rate: Number(stats.win_rate || 0),
  });
}


function paymentStatusLabel(status) {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'awaiting_payment') return 'Esperando pago';
  if (normalized === 'verification_in_progress') return 'Verificando';
  if (normalized === 'paid_unconfirmed') return 'Pago detectado';
  if (normalized === 'completed') return 'Completado';
  if (normalized === 'cancelled') return 'Cancelado';
  if (normalized === 'expired') return 'Expirado';
  return normalized.toUpperCase() || '—';
}

function paymentReasonLabel(reason) {
  const normalized = String(reason || '').toLowerCase();
  const mapping = {
    payment_not_found: 'Todavía no se detecta una transferencia exacta hacia la wallet receptora.',
    payment_waiting_confirmations: 'La transferencia fue detectada, pero aún no llega al mínimo de confirmaciones.',
    payment_confirmed: 'Pago confirmado correctamente.',
    verification_in_progress: 'Ya hay una verificación en curso para esta orden.',
    order_expired: 'La orden venció y debe generarse una nueva.',
    order_cancelled: 'La orden fue cancelada.',
    tx_already_used: 'La transacción ya fue utilizada en otra orden.',
    payment_config_missing: 'La configuración de cobro aún no está completa en el backend.',
  };
  return mapping[normalized] || 'Sin novedad adicional.';
}

function buildPaymentPlanCard(option, canCreate = true) {
  const article = document.createElement('article');
  article.className = 'kpi-card payment-plan-card';
  article.innerHTML = `
    <span class="kpi-label">Premium ${option.days} días</span>
    <div class="kpi-value">${formatNumber(option.price_usdt, 2)} USDT</div>
    <div class="kpi-subtext">Pago automático por USDT BEP-20 con monto único para identificar la orden.</div>
  `;
  const row = document.createElement('div');
  row.className = 'button-row';
  const button = document.createElement('button');
  button.className = 'primary-button';
  button.type = 'button';
  button.textContent = `Generar orden ${option.days}d`;
  button.disabled = !canCreate;
  button.addEventListener('click', () => createPaymentOrderAction(option.days));
  row.appendChild(button);
  article.appendChild(row);
  return article;
}

function renderBilling(data) {
  state.billing = data || null;
  const config = data?.configuration || {};
  const catalog = Array.isArray(data?.catalog?.premium) ? data.catalog.premium : [];
  if (elements.billingCatalogGrid) {
    elements.billingCatalogGrid.innerHTML = '';
    if (!catalog.length) {
      elements.billingCatalogGrid.appendChild(buildKpiCard('Catálogo', 'No disponible', 'No hay planes configurados todavía.'));
    } else {
      catalog.forEach((option) => elements.billingCatalogGrid.appendChild(buildPaymentPlanCard(option, Boolean(config.ready))));
    }
  }

  if (elements.billingConfigGrid) {
    const missing = Array.isArray(config.missing_keys) ? config.missing_keys : [];
    elements.billingConfigGrid.innerHTML = '';
    elements.billingConfigGrid.append(
      buildKpiCard('Estado', config.ready ? 'Listo' : 'Incompleto', config.ready ? 'El backend puede generar y verificar órdenes.' : 'Faltan variables críticas para cobros.'),
      buildKpiCard('Red', String(config.network || 'bep20').toUpperCase(), 'Canal de cobro configurado.'),
      buildKpiCard('Token', config.token_symbol || 'USDT', `Confirmaciones requeridas: ${data?.required_confirmations || 3}`),
      buildKpiCard('Pendientes', missing.length || 0, missing.length ? missing.join(', ') : 'Configuración completa.'),
    );
  }

  if (elements.billingHelpList) {
    elements.billingHelpList.className = 'list-stack';
    elements.billingHelpList.innerHTML = '';
    [
      '1. Genera una orden con el plan premium de 15 o 30 días.',
      '2. Envía exactamente el monto único mostrado a la wallet receptora en USDT BEP-20.',
      '3. Cuando la transferencia salga en cadena, pulsa Confirmar pago.',
      '4. Si todavía no hay confirmaciones suficientes, la orden quedará esperando y podrás volver a confirmar luego.',
    ].forEach((line) => {
      const item = document.createElement('article');
      item.className = 'list-item';
      item.innerHTML = `<div class="list-item-title">${line}</div>`;
      elements.billingHelpList.appendChild(item);
    });
  }

  const order = data?.active_order || null;
  if (elements.billingOrderBox) {
    if (!order) {
      elements.billingOrderBox.className = 'list-stack empty-state';
      elements.billingOrderBox.textContent = 'No hay una orden de pago activa ahora mismo.';
    } else {
      elements.billingOrderBox.className = 'list-stack';
      elements.billingOrderBox.innerHTML = `
        <article class="list-item">
          <div class="list-item-title">${paymentStatusLabel(order.status)} · Premium ${order.days} días</div>
          <div class="list-item-meta payment-order-detail">Monto exacto: ${order.amount_formatted || formatNumber(order.amount_usdt, 3)} ${order.token_symbol || 'USDT'} · Base ${formatNumber(order.base_price_usdt, 2)} USDT</div>
          ${buildCopyableAddress(order.deposit_address, 'Wallet receptora')}
          <div class="list-item-meta">Orden ${order.order_id} · vence ${formatDate(order.expires_at)}${Number.isFinite(order.expires_in_seconds) ? ' · ' + Math.max(0, Math.floor(order.expires_in_seconds / 60)) + ' min restantes' : ''}</div>
          <div class="list-item-meta">${paymentReasonLabel(order.last_verification_reason || order.status)}</div>
          ${order.matched_tx_hash ? `<div class="list-item-meta payment-order-detail">Tx detectada: ${order.matched_tx_hash}</div>` : ''}
        </article>
      `;
    }
  }

  const canConfirm = !!order && ['awaiting_payment', 'paid_unconfirmed'].includes(String(order.status || '').toLowerCase());
  const canCancel = !!order && ['awaiting_payment', 'verification_in_progress', 'paid_unconfirmed'].includes(String(order.status || '').toLowerCase());
  if (elements.billingConfirmButton) {
    elements.billingConfirmButton.disabled = !canConfirm;
    elements.billingConfirmButton.hidden = !order;
  }
  if (elements.billingCancelButton) {
    elements.billingCancelButton.disabled = !canCancel;
    elements.billingCancelButton.hidden = !order;
  }
}

function syncBillingActionButtons() {
  const order = state.billing?.active_order || null;
  const canConfirm = !!order && ['awaiting_payment', 'paid_unconfirmed'].includes(String(order.status || '').toLowerCase());
  const canCancel = !!order && ['awaiting_payment', 'verification_in_progress', 'paid_unconfirmed'].includes(String(order.status || '').toLowerCase());
  if (elements.billingConfirmButton) {
    elements.billingConfirmButton.disabled = !canConfirm;
    elements.billingConfirmButton.hidden = !order;
  }
  if (elements.billingCancelButton) {
    elements.billingCancelButton.disabled = !canCancel;
    elements.billingCancelButton.hidden = !order;
  }
}


function resolveRequestedTradingStatus(entity) {
  return entity?.trading_requested_status || entity?.trading_status || 'inactive';
}

function resolveEffectiveTradingLabel(entity) {
  return entity?.trading_effective_label || entity?.operational_label || entity?.trading_effective_status || entity?.trading_status || 'inactive';
}

function resolveEffectiveTradingDetail(entity) {
  return entity?.trading_effective_detail || entity?.operational_detail || 'Sin lectura operativa actual.';
}

function isTradingEffectivelyActive(entity) {
  const status = String(entity?.trading_effective_status || '').toLowerCase();
  return status === 'active' || status === 'manager_only';
}

function buildControlSummary(control) {
  const blockers = formatBlockers(control.activation_blockers_copy || control.activation_blockers);
  const exchange = control.exchange_snapshot || {};
  return [
    `Wallet: ${control.wallet_configured ? truncateMiddle(control.wallet_masked, 24) : 'pendiente'}`,
    `Private key: ${control.private_key_configured ? 'configurada' : 'pendiente'}`,
    `Políticas: ${control.terms_accepted ? 'confirmadas' : 'pendientes de confirmación'}`,
    `Plan: ${planLabel(control.plan)} (${control.plan_active ? 'activo' : 'inactivo'})`,
    `Días restantes: ${control.plan_days_remaining ?? 0}`,
    `Solicitud del usuario: ${control.trading_status || 'inactive'}`,
    `Estado real del motor: ${control.operational_label || 'Sin lectura'}`,
    `Modo operativo: ${control.operational_mode || 'unknown'}`,
    `Alineación: ${control.operational_alignment_label || 'Sin lectura'}`,
    `Preparación: ${control.readiness_completed ?? 0}/${control.readiness_total ?? 5}`,
    `Exchange: ${control.exchange_label || exchange.label || 'Sin lectura'}`,
    `Capital disponible: ${formatNumber(exchange.available_balance || 0, 4)} USDC`,
    `Equity: ${formatNumber(exchange.account_value || 0, 4)} USDC`,
    control.operational_detail ? `Detalle: ${control.operational_detail}` : null,
    (control.exchange_message || exchange.message) ? `Cuenta: ${control.exchange_message || exchange.message}` : null,
    control.runtime_checked_at ? `Última lectura: ${formatDate(control.runtime_checked_at)}` : null,
    '',
    'Bloqueadores:',
    blockers,
  ].filter(Boolean).join('\n');
}

function renderDashboard(data) {
  state.dashboard = data;
  const usernameText = data.username ? `@${data.username}` : `ID ${data.user_id || '—'}`;
  const readinessText = data.status_summary || 'not_ready';
  const tradingText = resolveRequestedTradingStatus(data);
  const effectiveTradingLabel = resolveEffectiveTradingLabel(data);
  const effectiveTradingDetail = resolveEffectiveTradingDetail(data);
  const planText = planLabel(data.plan);
  const exchange = data.exchange_snapshot || {};
  const operationalLabel = data.operational_label || effectiveTradingLabel;
  const accessText = accessLabel(data);
  const walletText = data.wallet_configured ? truncateMiddle(data.wallet || 'Configurada', 18) : 'Pendiente';
  if (elements.sidebarUser) elements.sidebarUser.textContent = usernameText;
  if (elements.sidebarPlan) elements.sidebarPlan.textContent = data.plan_active ? `Plan ${planText}` : 'Acceso limitado';

  setPill(elements.connectionBadge, 'Conectado', 'connected');
  setPill(elements.heroSessionPill, data.plan_active ? 'Sesión activa' : 'Acceso limitado', data.plan_active ? 'active' : 'blocked');
  setPill(elements.heroPlanPill, `Plan ${planText}`, data.plan_active ? data.plan : 'none');
  setPill(elements.heroTradingPill, exchange.label || operationalLabel, exchange.tone || data.operational_tone || tradingText);
  setPill(elements.userPlanBadge, planText, data.plan_active ? data.plan : 'none');
  setPill(elements.userReadinessBadge, readinessText, readinessText);
  setPill(elements.walletBadge, data.wallet_configured ? 'Wallet ok' : 'Pendiente', data.wallet_configured ? 'configured' : 'missing');

  elements.heroUser.textContent = usernameText;
  elements.heroUserSubtext.textContent = data.plan_active ? `Usuario autenticado · preparación ${data.readiness_completed || 0}/${data.readiness_total || 5}` : 'Sin acceso operativo activo';
  elements.heroWallet.textContent = walletText;
  elements.heroWalletSubtext.textContent = data.wallet_configured ? 'Configurada en backend' : 'Todavía falta configurarla';
  elements.heroKey.textContent = data.private_key_configured ? 'Configurada' : 'Pendiente';
  elements.heroKeySubtext.textContent = data.private_key_configured ? 'Llave privada presente en sistema' : 'Llave privada faltante';
  elements.heroAccess.textContent = accessText;
  const capitalDetail = exchange.available_balance != null ? ` · capital ${formatNumber(exchange.available_balance || 0, 4)} USDC` : '';
  elements.heroAccessSubtext.textContent = `${accessDetail(data)}${data.plan_expires_at ? ' · vence ' + formatDate(data.plan_expires_at) : ''}${capitalDetail}`;

  elements.userIdentity.textContent = usernameText;
  elements.userIdentitySubtext.textContent = data.user_id ? `Telegram ID ${data.user_id}` : 'Usuario no resuelto';
  elements.userReadiness.textContent = readinessText;
  elements.userTradingStatus.textContent = `${effectiveTradingLabel} · ${effectiveTradingDetail}`;
  elements.walletConfigured.textContent = data.wallet_configured ? 'Wallet configurada' : 'Wallet pendiente';
  elements.privateKeyConfigured.textContent = data.private_key_configured ? 'Private key cargada' : 'Private key faltante';

  elements.dashboardStats.innerHTML = '';
  elements.dashboardStats.append(
    buildKpiCard('Plan', planText, data.plan_active ? 'Acceso operativo habilitado.' : 'Acceso inactivo.'),
    buildKpiCard('Días restantes', data.plan_days_remaining ?? 0, data.plan_active ? 'Se calculan sobre el vencimiento actual.' : 'Sin días vigentes.'),
    buildKpiCard('Trading solicitado', tradingText, 'Estado deseado guardado por el usuario.'),
    buildKpiCard('Estado visible', effectiveTradingLabel, effectiveTradingDetail),
    buildKpiCard('Motor real', operationalLabel, data.operational_detail || 'Lectura operativa del motor.'),
    buildKpiCard('Políticas', data.terms_accepted ? 'Confirmadas' : 'Pendientes', 'Bloquean la activación si falta la confirmación.'),
    buildKpiCard('Wallet', data.wallet_configured ? 'Sí' : 'No', 'Configuración sensible del usuario.'),
    buildKpiCard('Private key', data.private_key_configured ? 'Sí' : 'No', 'No se vuelve a exponer por API.'),
    buildKpiCard('Vencimiento', formatDate(data.plan_expires_at), 'Fecha actual del plan.'),
    buildKpiCard('Exchange', exchange.label || 'Sin lectura', exchange.message || 'Sin lectura del exchange.'),
    buildKpiCard('Capital disponible', `${formatNumber(exchange.available_balance || 0, 4)} USDC`, exchange.capital_sufficient ? 'Capital suficiente para operar.' : `Mínimo ${formatNumber(exchange.capital_threshold || 0, 2)} USDC.`),
    buildKpiCard('Equity', `${formatNumber(exchange.account_value || 0, 4)} USDC`, exchange.has_open_position ? `Posiciones abiertas: ${exchange.positions_count || 0}` : 'Sin posiciones abiertas en este momento.')
  );

  elements.readinessList.innerHTML = '';
  elements.readinessList.append(
    buildReadinessItem('Wallet', data.wallet_configured ? 'La wallet ya está guardada.' : 'Todavía no existe wallet guardada.', data.wallet_configured ? 'configured' : 'missing'),
    buildReadinessItem('Private key', data.private_key_configured ? 'La private key ya existe.' : 'Todavía falta private key.', data.private_key_configured ? 'configured' : 'missing'),
    buildReadinessItem('Políticas', data.terms_accepted ? 'La confirmación de políticas ya fue registrada.' : 'Debes confirmar la aceptación de políticas para activar trading.', data.terms_accepted ? 'active' : 'warning'),
    buildReadinessItem('Plan', data.plan_active ? `El plan ${planText} está activo.` : 'No hay acceso operativo vigente.', data.plan_active ? 'active' : 'inactive')
  );

  if (elements.accessStatsGrid) {
    elements.accessStatsGrid.innerHTML = '';
    elements.accessStatsGrid.append(
      buildKpiCard('Estado de acceso', accessText, accessDetail(data)),
      buildKpiCard('Días restantes', data.plan_days_remaining ?? 0, data.plan_active ? 'Continuidad actual del plan.' : 'No hay días vigentes.'),
      buildKpiCard('Vencimiento', formatDate(data.plan_expires_at), data.plan_active ? 'Fecha actual del acceso.' : 'No existe acceso operativo vigente.'),
      buildKpiCard('Capital disponible', `${formatNumber(exchange.available_balance || 0, 4)} USDC`, exchange.message || 'Lectura viva del exchange.'),
      buildKpiCard('Estado de cuenta', exchange.label || 'Sin lectura', exchange.has_open_position ? `Posiciones abiertas ${exchange.positions_count || 0}` : 'Sin posiciones abiertas.'),
    );
  }
}

function renderControl(control) {
  state.control = control;
  elements.controlReadinessBox.textContent = buildControlSummary(control);
  elements.termsStatusText.textContent = control.terms_accepted ? 'Confirmadas' : 'Pendientes de confirmación';
  elements.acceptTermsButton.disabled = control.terms_accepted;
  elements.activateTradingButton.disabled = !control.activation_ready || isTradingEffectivelyActive(control);
  elements.pauseTradingButton.disabled = resolveRequestedTradingStatus(control) !== 'active';

  if (!elements.walletInput.value && control.wallet) {
    elements.walletInput.value = control.wallet;
  }

  elements.controlStats.innerHTML = '';
  const exchange = control.exchange_snapshot || {};
  elements.controlStats.append(
    buildKpiCard('Wallet', control.wallet_configured ? truncateMiddle(control.wallet_masked, 18) : 'Pendiente', 'Dirección actual del usuario.'),
    buildKpiCard('Private key', control.private_key_configured ? 'Configurada' : 'Pendiente', control.security_posture === 'encrypted_at_rest' ? 'Cifrada en reposo.' : 'Nunca se reexpone por API.'),
    buildKpiCard('Seguridad', control.security_posture === 'encrypted_at_rest' ? 'Cifrada' : (control.security_posture === 'legacy_plaintext' ? 'Legacy' : 'Sin key'), control.security_posture === 'legacy_plaintext' ? 'Requiere rotación para endurecer.' : 'Estado del almacenamiento sensible.'),
    buildKpiCard('Políticas', control.terms_accepted ? 'Confirmadas' : 'Pendientes', 'Bloquean la activación si falta la confirmación.'),
    buildKpiCard('Solicitud', resolveRequestedTradingStatus(control), 'Estado deseado por el usuario desde la MiniApp.'),
    buildKpiCard('Estado visible', resolveEffectiveTradingLabel(control), resolveEffectiveTradingDetail(control)),
    buildKpiCard('Estado real', control.operational_label || 'Sin lectura', control.operational_detail || 'Lectura operativa actual.'),
    buildKpiCard('Modo operativo', control.operational_mode || 'unknown', control.operational_live_trade ? `Trade vivo ${control.operational_active_symbol || ''}`.trim() : 'Sin trade activo gestionado ahora mismo.'),
    buildKpiCard('Plan', planLabel(control.plan), control.plan_active ? `Plan vigente · ${control.plan_days_remaining || 0} día(s) restantes.` : 'Sin acceso vigente.'),
    buildKpiCard('Alineación', control.operational_alignment_label || 'Sin lectura', control.runtime_checked_at ? `Última lectura ${formatDate(control.runtime_checked_at)}` : 'Todavía no hay lectura del motor.'),
    buildKpiCard('Preparación', `${control.readiness_completed || 0}/${control.readiness_total || 5}`, 'Lectura global de requisitos cumplidos.'),
    buildKpiCard('Activación', control.activation_ready ? 'Lista' : 'Bloqueada', (control.activation_blockers_copy || []).join(' ') || 'Sin bloqueadores.'),
    buildKpiCard('Exchange', control.exchange_label || exchange.label || 'Sin lectura', control.exchange_message || exchange.message || 'Sin lectura viva del exchange.'),
    buildKpiCard('Capital disponible', `${formatNumber(exchange.available_balance || 0, 4)} USDC`, exchange.capital_sufficient ? 'Capital suficiente para abrir nuevas entradas.' : `Mínimo configurado ${formatNumber(exchange.capital_threshold || 0, 2)} USDC.`),
    buildKpiCard('Equity total', `${formatNumber(exchange.account_value || 0, 4)} USDC`, exchange.exchange_reachable ? 'Valor total leído desde Hyperliquid.' : 'No se pudo consultar el exchange.'),
    buildKpiCard('Posiciones abiertas', exchange.positions_count || 0, exchange.has_open_position ? (exchange.active_symbols || []).join(', ') || 'Hay una posición bajo gestión.' : 'Sin posiciones abiertas ahora mismo.')
  );
}

function renderEquityPulse(trades = []) {
  const container = document.getElementById('equityChart');
  if (!container) return;
  const rows = Array.isArray(trades) ? trades.filter(Boolean).slice().reverse() : [];
  const values = [];
  let running = 0;
  rows.forEach((trade) => {
    const profit = Number(trade.profit ?? trade.pnl ?? 0);
    if (Number.isFinite(profit)) {
      running += profit;
      values.push(running);
    }
  });
  if (!values.length) {
    container.innerHTML = '<div class="chart-empty">Sin suficientes operaciones visibles para trazar la curva.</div>';
    return;
  }
  const w = 900, h = 280, padX = 22, padY = 26;
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const range = Math.max(max - min, 0.0001);
  const x = (i) => padX + (i * (w - padX * 2) / Math.max(values.length - 1, 1));
  const y = (v) => h - padY - ((v - min) / range) * (h - padY * 2);
  const points = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const area = `${padX},${h-padY} ${points} ${x(values.length - 1)},${h-padY}`;
  const zeroY = y(0);
  const finalValue = values[values.length - 1];
  const tone = finalValue >= 0 ? '#25e0a1' : '#ff5c7c';
  container.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="PnL acumulado">
      <defs>
        <linearGradient id="equityFill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="${tone}" stop-opacity=".22" />
          <stop offset="100%" stop-color="${tone}" stop-opacity="0" />
        </linearGradient>
      </defs>
      <line x1="${padX}" y1="${zeroY}" x2="${w-padX}" y2="${zeroY}" stroke="rgba(132,156,190,.16)" stroke-dasharray="4 6" />
      <polygon points="${area}" fill="url(#equityFill)" />
      <polyline points="${points}" fill="none" stroke="${tone}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
      <circle cx="${x(values.length - 1)}" cy="${y(finalValue)}" r="5" fill="${tone}" />
      <circle cx="${x(values.length - 1)}" cy="${y(finalValue)}" r="11" fill="${tone}" opacity=".12" />
      <text x="${w-padX}" y="18" text-anchor="end" fill="#7f92a8" font-size="11">Acumulado ${formatNumber(finalValue, 4)}</text>
    </svg>
  `;
}

function renderPerformanceWindowChart(performance = {}) {
  const container = document.getElementById('performanceWindowChart');
  if (!container) return;
  const rows = ['24h', '7d', '30d'].map((key) => ({ key, value: Number(performance?.[key]?.pnl_total ?? 0) }));
  const maxAbs = Math.max(...rows.map((row) => Math.abs(row.value)), 0.0001);
  container.innerHTML = rows.map((row) => {
    const pct = Math.max(6, Math.min(100, Math.abs(row.value) / maxAbs * 100));
    const negative = row.value < 0;
    return `<div class="bar-row"><span class="bar-label">${row.key}</span><div class="bar-track"><div class="bar-fill ${negative ? 'negative' : ''}" style="width:${pct.toFixed(1)}%"></div></div><span class="bar-value">${negative ? '' : '+'}${formatNumber(row.value, 4)}</span></div>`;
  }).join('');
}

function renderPerformance(performance) {
  state.performance = performance;
  renderPerformanceWindowChart(performance);
  elements.performanceGrid.innerHTML = '';
  elements.performanceGrid.append(
    buildPerformanceCard('24h', performance['24h'] || {}),
    buildPerformanceCard('7d', performance['7d'] || {}),
    buildPerformanceCard('30d', performance['30d'] || {})
  );

  if (elements.performanceExecutiveGrid) {
    const executive = performance.executive || {};
    const track = performance.track_record || {};
    elements.performanceExecutiveGrid.innerHTML = '';
    elements.performanceExecutiveGrid.append(
      buildKpiCard('Mejor ventana', executive.best_window || 'Sin muestra', executive.best_window ? `PnL ${formatNumber(executive.best_window_pnl || 0, 4)}` : 'Todavía no hay una ventana con trades suficientes.'),
      buildKpiCard('Lectura actual', executive.edge_label || 'Sin lectura', executive.edge_detail || 'Sin lectura ejecutiva disponible.'),
      buildKpiCard('Trades 30d', executive.trades_30d || 0, `Cadencia ${formatNumber(executive.cadence_30d || 0, 2)} trades/día · Decisivos ${executive.decisive_30d || 0}`),
      buildKpiCard('Expectativa', formatNumber(executive.expectancy || track.expectancy || 0, 4), `Avg win ${formatNumber(executive.avg_win || track.avg_win || 0, 4)} · Avg loss ${formatNumber(executive.avg_loss || track.avg_loss || 0, 4)}`),
      buildKpiCard('Racha actual', executive.streak_label || 'Sin racha', `Mejor win x${executive.streak_best_win || 0} · Mejor loss x${executive.streak_best_loss || 0}`),
      buildKpiCard('Forma reciente', executive.recent_form_compact || track.recent_form_compact || '—', executive.dominant_symbol ? `${executive.dominant_symbol} · ${executive.dominant_symbol_count || 0} trade(s)` : 'Sin símbolo dominante todavía.'),
      buildKpiCard('Track record', track.total || 0, `Win ${formatNumber(track.win_rate || 0, 2)}% · PF ${track.profit_factor === Infinity ? '∞' : formatNumber(track.profit_factor || 0, 2)}`),
      buildKpiCard('Extremos', `${formatNumber(track.best_trade || 0, 4)} / ${formatNumber(track.worst_trade || 0, 4)}`, track.last_trade_at ? `Último cierre ${formatDate(track.last_trade_at)}` : 'Sin cierres históricos todavía.')
    );
  }
}

// Comparte únicamente el registro recibido para la sesión actual; el módulo
// exporta una imagen, nunca credenciales ni identificadores de cuenta.
const shareQuotes = new Map();
function shareTrade(kind, trade) {
  let current = trade;
  if (kind === 'live-open') {
    current = state.operations?.active_trade || trade;
    const coin = String(current?.symbol || current?.coin || '').toUpperCase();
    const quote = shareQuotes.get(coin);
    if (quote && Date.now() - quote.receivedAt < 15_000) {
      const side = String(current?.direction || current?.side || '').toUpperCase();
      // Para cerrar un LONG se vende al bid; para cerrar un SHORT se compra al ask.
      const exit = ['LONG','BUY','B'].includes(side) ? quote.bid : ['SHORT','SELL','S'].includes(side) ? quote.ask : null;
      if (exit) current = { ...current, share_quote_exit_price: exit };
    }
  }
  window.dispatchEvent(new CustomEvent('hyperleague:share-trade', {
    detail: { kind, trade: current, referrals: state.referrals },
  }));
}

function tradeShareButton(kind, trade, label = 'Compartir operación') {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'secondary-button trade-share-trigger';
  button.textContent = label;
  button.addEventListener('click', () => shareTrade(kind, trade));
  return button;
}

function renderOperations(data) {
  state.operations = data;
  renderEquityPulse(data?.trades || []);
  const summary = data.summary || {};
  const timeline = Array.isArray(data.activity) ? data.activity : [];
  const timelineSummary = data.timeline_summary || {};

  setPill(elements.activeTradeBadge, data.active_trade_summary ? 'LIVE' : 'IDLE', data.active_trade_summary ? 'active' : 'neutral');
  renderLiveTradeSummary(elements.activeTradeSummary, data.active_trade_summary, 'No hay una operación activa registrada ahora mismo.');
  // No rellenar niveles o PnL LIVE ausentes con cifras supuestas.
  if (data.active_trade && elements.activeTradeSummary) {
    elements.activeTradeSummary.appendChild(tradeShareButton('live-open', data.active_trade, 'Compartir operación LIVE'));
  }

  if (elements.operationsSummaryGrid) {
    elements.operationsSummaryGrid.innerHTML = '';
    elements.operationsSummaryGrid.append(
      buildKpiCard('Wins visibles', summary.wins || 0, 'Trades ganadores en la lista visible.'),
      buildKpiCard('Losses visibles', summary.losses || 0, 'Trades perdedores en la lista visible.'),
      buildKpiCard('Neto visible', formatNumber(summary.net_visible || 0, 4), 'Suma del PnL en la muestra actual.'),
      buildKpiCard('Expectativa visible', formatNumber(summary.avg_trade_visible || 0, 4), 'Promedio de PnL por trade en la muestra visible.'),
      buildKpiCard('Racha visible', summary.current_streak_count ? `${String(summary.current_streak_type || '').toUpperCase()} x${summary.current_streak_count}` : 'Sin racha', 'Cuenta consecutiva desde el trade más reciente.'),
      buildKpiCard('Forma reciente', summary.recent_form_visible || '—', summary.dominant_symbol ? `${summary.dominant_symbol} · ${summary.dominant_symbol_count || 0} trade(s)` : 'Sin símbolo dominante visible.'),
      buildKpiCard('Símbolo dominante', summary.dominant_symbol || '—', summary.dominant_symbol ? `PnL ${formatNumber(summary.dominant_symbol_pnl || 0, 4)}` : 'La muestra visible todavía es muy corta.'),
      buildKpiCard('Mejor / peor', `${formatNumber(summary.best_trade_pnl || 0, 4)} / ${formatNumber(summary.worst_trade_pnl || 0, 4)}`, 'Extremos dentro de la lista visible.'),
    );
  }

  if (elements.operationsTimelineGrid) {
    elements.operationsTimelineGrid.innerHTML = '';
    elements.operationsTimelineGrid.append(
      buildKpiCard('Eventos visibles', timelineSummary.total_visible_events || 0, 'Actividad reciente registrada para esta cuenta.'),
      buildKpiCard('Trading', timelineSummary.trading_events || 0, 'Aperturas y cierres visibles en el feed.'),
      buildKpiCard('Cuenta', timelineSummary.account_events || 0, 'Cambios de wallet, key, políticas o acceso.'),
      buildKpiCard('Control', timelineSummary.control_events || 0, timelineSummary.live_trade ? 'Hay una operación viva registrada.' : 'Sin operación activa ahora mismo.'),
    );
  }

  renderEventSummary(elements.latestOpenSummary, 'Última apertura', data.last_open_summary, 'Sin aperturas registradas.');
  renderEventSummary(elements.latestCloseSummary, 'Último cierre', data.last_close_summary, 'Sin cierres registrados.');

  if (elements.userActivityList) {
    if (!timeline.length) {
      elements.userActivityList.className = 'list-stack empty-state';
      elements.userActivityList.textContent = 'Sin actividad reciente.';
    } else {
      elements.userActivityList.className = 'list-stack';
      elements.userActivityList.innerHTML = '';
      timeline.slice(0, 6).forEach((item) => {
        const article = document.createElement('article');
        article.className = 'list-item';
        article.innerHTML = `
          <div class="list-item-header">
            <div>
              <div class="list-item-title">${item.title || 'Actividad'}</div>
              <div class="list-item-meta">${item.at ? formatDate(item.at) : 'Sin timestamp'}</div>
            </div>
            <span class="status-pill ${pillClass(item.tone || 'neutral')}">${item.badge || 'INFO'}</span>
          </div>
          <div class="list-item-meta">${formatActivityDetail(item)}</div>
        `;
        elements.userActivityList.appendChild(article);
      });
    }
  }

  if (elements.timelineCount) {
    elements.timelineCount.textContent = String(timeline.length || 0);
  }

  if (elements.operationsTimelineList) {
    if (!timeline.length) {
      elements.operationsTimelineList.className = 'list-stack empty-state';
      elements.operationsTimelineList.textContent = 'Sin actividad reciente.';
    } else {
      elements.operationsTimelineList.className = 'list-stack';
      elements.operationsTimelineList.innerHTML = '';
      timeline.forEach((item) => {
        const article = document.createElement('article');
        article.className = 'list-item';
        article.innerHTML = `
          <div class="list-item-header">
            <div>
              <div class="list-item-title">${item.title || 'Actividad'}</div>
              <div class="list-item-meta">${item.at ? formatDate(item.at) : 'Sin timestamp'} · ${item.family || 'info'}</div>
            </div>
            <span class="status-pill ${pillClass(item.tone || 'neutral')}">${item.badge || 'INFO'}</span>
          </div>
          <div class="list-item-meta">${formatActivityDetail(item)}</div>
        `;
        elements.operationsTimelineList.appendChild(article);
      });
    }
  }

  elements.operationsCount.textContent = String(data.count || 0);

  if (!Array.isArray(data.trades) || data.trades.length === 0) {
    elements.operationsList.className = 'list-stack empty-state';
    elements.operationsList.textContent = 'Sin operaciones recientes.';
    return;
  }

  elements.operationsList.className = 'list-stack';
  elements.operationsList.innerHTML = '';

  data.trades.forEach((trade) => {
    const item = document.createElement('article');
    item.className = 'list-item';

    const profit = Number(trade.profit || 0);
    const profitLabel = Number.isFinite(profit) ? profit.toFixed(4) : String(trade.profit || '0');
    const badgeClass = trade.result_tone || (profit > 0 ? 'success' : (profit < 0 ? 'danger' : 'neutral'));

    item.innerHTML = `
      <div class="list-item-header">
        <div>
          <div class="list-item-title">${trade.symbol || 'Trade'} · ${trade.side || 'N/A'}</div>
          <div class="list-item-meta">${formatDate(trade.timestamp)} · ${trade.result_label || 'Trade'}</div>
        </div>
        <span class="status-pill ${badgeClass}">${profitLabel}</span>
      </div>
      <div class="list-item-grid">
        <div><span class="metric-label">Entry</span><strong>${trade.entry_price ?? '—'}</strong></div>
        <div><span class="metric-label">Exit</span><strong>${trade.exit_price ?? '—'}</strong></div>
        <div><span class="metric-label">Qty</span><strong>${trade.qty ?? '—'}</strong></div>
        <div><span class="metric-label">Valor</span><strong>${trade.notional_usdc != null ? `${formatNumber(trade.notional_usdc, 4)} USDC` : '—'}</strong></div>
        <div><span class="metric-label">PnL bruto</span><strong>${formatNumber(trade.gross_pnl ?? 0, 4)}</strong></div>
        <div><span class="metric-label">Fees</span><strong>${formatNumber(trade.fees ?? 0, 4)}</strong></div>
        <div><span class="metric-label">PnL neto</span><strong>${formatNumber(trade.profit ?? 0, 4)}</strong></div>
        <div><span class="metric-label">Score</span><strong>${trade.best_score ?? '—'}</strong></div>
        <div><span class="metric-label">Razón</span><strong>${trade.exit_reason || trade.close_source || '—'}</strong></div>
        <div><span class="metric-label">Fuente</span><strong>${trade.pnl_source || '—'}</strong></div>
      </div>
    `;

    item.appendChild(tradeShareButton('live-closed', trade, 'Compartir resultado'));
    elements.operationsList.appendChild(item);
  });
}

function resolveReferralLink(data) {
  const directLink = String(data.referral_link || '').trim();
  if (directLink) return directLink;
  const botUsername = String(data.bot_username || 'TradingXHiperPro_bot').trim().replace(/^@+/, '');
  const referralCode = String(data.referral_code || data.user_id || '').trim();
  if (!botUsername || !referralCode) return '';
  return `https://t.me/${botUsername}?start=${referralCode}`;
}

function buildReferralLinkCard(data) {
  const article = document.createElement('article');
  article.className = 'kpi-card referral-link-card';

  const referralLink = resolveReferralLink(data);
  const referralCode = String(data.referral_code || data.user_id || '').trim();
  const escapedLink = escapeHtml(referralLink);
  const escapedCode = escapeHtml(referralCode);
  const botUsername = escapeHtml(String(data.bot_username || 'TradingXHiperPro_bot').trim().replace(/^@+/, ''));
  const safeHref = referralLink ? escapeHtml(referralLink) : '';

  article.innerHTML = `
    <span class="kpi-label">Enlace referido</span>
    <div class="payment-copy-block">
      <span class="payment-copy-label">Este es el enlace que el usuario debe copiar y compartir.</span>
      <div class="payment-copy-row referral-link-row">
        <span class="payment-copy-value referral-link-value" title="${escapedLink}">${escapedLink || 'No disponible todavía.'}</span>
      </div>
      <div class="payment-copy-row referral-action-row">
        <button class="inline-copy-button" type="button" data-copy-text="${escapedLink}" data-copy-success="Enlace referido copiado." ${referralLink ? '' : 'disabled'}>Copiar enlace</button>
        <button class="inline-copy-button" type="button" data-action="share-referral" ${referralLink ? '' : 'disabled'}>Compartir</button>
        ${referralLink ? `<a class="inline-copy-button referral-open-link" href="${safeHref}" target="_blank" rel="noopener noreferrer">Abrir enlace</a>` : ''}
      </div>
    </div>
    <div class="payment-copy-block">
      <span class="payment-copy-label">Código referido</span>
      <div class="payment-copy-row">
        <span class="payment-copy-value" title="${escapedCode}">${escapedCode || '—'}</span>
        <button class="inline-copy-button" type="button" data-copy-text="${escapedCode}" data-copy-success="Código referido copiado." ${referralCode ? '' : 'disabled'}>Copiar código</button>
      </div>
    </div>
    <div class="kpi-subtext">Bot: @${botUsername}</div>
  `;
  return article;
}

function buildReferralProgramCard(data) {
  const article = document.createElement('article');
  article.className = 'kpi-card referral-program-card';

  const rewardRows = Array.isArray(data.reward_table)
    ? data.reward_table.map((item) => `<div class="kpi-subtext">${escapeHtml(item.purchase_label || 'Premium')} → <strong>${escapeHtml(item.reward_label || 'Sin recompensa')}</strong></div>`).join('')
    : '';

  article.innerHTML = `
    <span class="kpi-label">Reglas del programa</span>
    <div class="kpi-subtext">${escapeHtml(data.valid_referral_rule || 'El referido cuenta cuando compra un plan válido.')}</div>
    <div class="kpi-subtext">${escapeHtml(data.reward_rule || 'La recompensa se aplica automáticamente en Premium.')}</div>
    ${rewardRows}
  `;
  return article;
}

function renderReferrals(data) {
  state.referrals = data;
  elements.referralStats.innerHTML = '';
  elements.referralStats.append(
    buildReferralLinkCard(data),
    buildKpiCard('Referidos válidos', data.referral_valid_count || 0, 'Referidos válidos acreditados una sola vez.'),
    buildKpiCard('Código referido', data.referral_code || data.user_id || '—', `Bot: @${data.bot_username || 'TradingXHiperPro_bot'}`),
    buildReferralProgramCard(data),
  );
}

function renderSystemRuntime(payload) {
  state.systemRuntime = payload;
  renderPublicSystemOverview(payload);
  setSummarySystemVisibility();

  const hasTechnicalDetails = Boolean(payload.components && Object.keys(payload.components).length);

  if (elements.systemTabButton) {
    elements.systemTabButton.classList.toggle('hidden', !hasTechnicalDetails);
  }

  if (!hasTechnicalDetails) {
    if (elements.systemHealthGridPanel) elements.systemHealthGridPanel.innerHTML = '';
    if (elements.systemRuntimeGridPanel) elements.systemRuntimeGridPanel.innerHTML = '';
    if (elements.backendHealthCard) elements.backendHealthCard.innerHTML = '';
    if (elements.systemActivityList) {
      elements.systemActivityList.className = 'list-stack empty-state';
      elements.systemActivityList.textContent = 'Los detalles técnicos solo están disponibles para administración.';
    }
    return;
  }

  renderTechnicalSystemPanel(payload);
}



function renderActivityList(container, items, emptyText, options = {}) {
  if (!container) return;
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) {
    const emptyTitle = options.emptyTitle || 'Sin actividad';
    const emptyDetail = options.emptyDetail || emptyText || 'No hay elementos para mostrar.';
    container.className = 'list-stack empty-state';
    container.innerHTML = `<div class="empty-title">${emptyTitle}</div><div class="empty-detail">${emptyDetail}</div>`;
    return;
  }

  container.className = 'list-stack';
  container.innerHTML = '';
  rows.forEach((row) => {
    const article = document.createElement('article');
    article.className = 'activity-item';

    const tone = String(row?.tone || row?.status || row?.event_type || 'info').toLowerCase();
    const username = row?.username ? `@${row.username}` : (row?.user_id ? `Usuario ${row.user_id}` : 'Sistema');
    const title = row?.title || row?.message || row?.action || row?.event_type || 'Evento';
    const target = row?.target_username ? ` · @${row.target_username}` : (row?.target_user_id ? ` · Usuario ${row.target_user_id}` : '');
    const detailParts = [];
    if (row?.detail) detailParts.push(row.detail);
    if (row?.reason) detailParts.push(`Motivo: ${row.reason}`);
    if (row?.metadata?.symbol) detailParts.push(`Símbolo ${row.metadata.symbol}`);
    if (row?.metadata?.result) detailParts.push(`Resultado ${row.metadata.result}`);
    if (!detailParts.length && row?.event_type) detailParts.push(`Tipo ${row.event_type}`);
    const detail = detailParts.join(' · ') || 'Sin detalle adicional.';
    const when = row?.created_at || row?.occurred_at || row?.opened_at || row?.timestamp;

    article.innerHTML = `
      <div class="activity-dot ${pillClass(tone)}"></div>
      <div class="activity-copy">
        <div class="activity-title">${escapeHtml(title)} · ${escapeHtml(username)}${escapeHtml(target)}</div>
        <div class="activity-detail">${escapeHtml(detail)}</div>
      </div>
      <div class="activity-time">${formatDate(when)}</div>
    `;
    container.appendChild(article);
  });
}

function renderAdminMonitorFeed(rows) {
  renderActivityList(
    elements.adminMonitorFeed,
    rows || [],
    'Sin eventos operativos recientes.',
    {
      emptyTitle: 'Sin eventos recientes',
      emptyDetail: 'Todavía no hay aperturas, cierres o pagos recientes para mostrar.',
    }
  );
}

function renderAdminLivePositions(rows) {
  if (!elements.adminLivePositions) return;
  const items = Array.isArray(rows) ? rows : [];
  if (!items.length) {
    elements.adminLivePositions.className = 'list-stack empty-state';
    elements.adminLivePositions.textContent = 'Sin posiciones activas ahora mismo.';
    return;
  }
  elements.adminLivePositions.className = 'list-stack';
  elements.adminLivePositions.innerHTML = '';
  items.forEach((row) => {
    const article = document.createElement('article');
    article.className = 'activity-item';
    const userLabel = row.username ? `@${row.username}` : `Usuario ${row.user_id || '—'}`;
    const symbol = row.symbol || '—';
    const side = String(row.direction || row.side || '').toUpperCase() || '—';
    const detailBits = [];
    if (row.entry_price !== null && row.entry_price !== undefined) detailBits.push(`Entry ${formatNumber(row.entry_price, 6)}`);
    if (row.qty_coin !== null && row.qty_coin !== undefined) detailBits.push(`Qty ${formatNumber(row.qty_coin, 6)}`);
    if (row.qty_usdc !== null && row.qty_usdc !== undefined) detailBits.push(`Notional ${formatNumber(row.qty_usdc, 2)} USDT`);
    if (row.manager_heartbeat_at) detailBits.push(`Heartbeat ${formatDate(row.manager_heartbeat_at)}`);
    article.innerHTML = `
      <div class="activity-dot info"></div>
      <div class="activity-copy">
        <div class="activity-title">${symbol} · ${side} · ${userLabel}</div>
        <div class="activity-detail">${detailBits.join(' · ') || 'Posición activa detectada en runtime.'}</div>
      </div>
      <div class="activity-time">${formatDate(row.opened_at)}</div>
    `;
    elements.adminLivePositions.appendChild(article);
  });
}

function renderAdminComponentTelemetry(systemRuntime) {
  if (!elements.adminComponentTelemetry) return;
  const components = (systemRuntime && systemRuntime.components) || {};
  const backendIdentity = (systemRuntime && systemRuntime.backend_identity) || {};
  const bridge = (systemRuntime && systemRuntime.bridge_diagnostics) || {};
  const list = [
    ['telegram_bot', 'Canal Telegram'],
    ['trading_loop', 'Motor DEMO'],
    ['scanner', 'Scanner DEMO'],
  ];
  elements.adminComponentTelemetry.innerHTML = '';

  const backendBits = [];
  if (backendIdentity.process_role) backendBits.push(`Rol ${backendIdentity.process_role}`);
  if (backendIdentity.runtime_instance) backendBits.push(`Instancia ${backendIdentity.runtime_instance}`);
  if (backendIdentity.db_name) backendBits.push(`DB ${backendIdentity.db_name}`);
  if (backendIdentity.mongo_target) backendBits.push(`Mongo ${backendIdentity.mongo_target}`);
  elements.adminComponentTelemetry.append(
    buildKpiCard('Backend MiniApp', String((systemRuntime && systemRuntime.overall_status) || 'unknown').toUpperCase(), backendBits.join(' · ') || 'Sin identidad backend.')
  );

  const bridgeBits = [];
  if (Array.isArray(bridge.missing_components) && bridge.missing_components.length) bridgeBits.push(`Faltan ${bridge.missing_components.join(', ')}`);
  if (bridge.runtime_status_count !== null && bridge.runtime_status_count !== undefined) bridgeBits.push(`runtime_status ${bridge.runtime_status_count}`);
  if (bridge.runtime_components_shadow_count !== null && bridge.runtime_components_shadow_count !== undefined) bridgeBits.push(`shadow ${bridge.runtime_components_shadow_count}`);
  if (Array.isArray(bridge.hints) && bridge.hints.length) bridgeBits.push(bridge.hints[0]);
  if (!bridgeBits.length && bridge.message) bridgeBits.push(bridge.message);
  elements.adminComponentTelemetry.append(
    buildKpiCard('Puente runtime', String((bridge.status || 'unknown')).toUpperCase(), bridgeBits.join(' · ') || 'Sin diagnóstico del puente runtime.')
  );

  list.forEach(([key, label]) => {
    const item = components[key] || {};
    const status = item.status || 'offline';
    const meta = item.metadata || {};
    const freshness = item.freshness_seconds === null || item.freshness_seconds === undefined ? 'sin lectura' : `${item.freshness_seconds}s`;
    const sub = [];
    if (meta.symbol) sub.push(`Símbolo ${meta.symbol}`);
    if (meta.last_decision) sub.push(`Decisión ${meta.last_decision}`);
    const capital = meta.available_balance ?? meta.exchange_available_balance;
    if (capital !== null && capital !== undefined) sub.push(`Capital ${formatNumber(capital, 4)} USDT`);
    if (meta.users_loaded !== null && meta.users_loaded !== undefined) sub.push(`Usuarios ${meta.users_loaded}`);
    if (meta.phase) sub.push(`Fase ${meta.phase}`);
    if (meta.runtime_source) sub.push(`Fuente ${meta.runtime_source}`);
    if (meta.writer_process_role) sub.push(`Writer ${meta.writer_process_role}`);
    if (meta.writer_runtime_instance) sub.push(`Instancia ${meta.writer_runtime_instance}`);
    if (meta.writer_db_name) sub.push(`DB writer ${meta.writer_db_name}`);
    if (!sub.length && item.message) sub.push(item.message);
    elements.adminComponentTelemetry.append(
      buildKpiCard(label, String(status).toUpperCase(), `${sub.join(' · ') || 'Sin lectura útil.'} · Frescura ${freshness}`)
    );
  });
}

function renderAdminOperatorSnapshots(rows) {
  if (!elements.adminOperatorSnapshots) return;
  const items = Array.isArray(rows) ? rows : [];
  if (!items.length) {
    elements.adminOperatorSnapshots.className = 'list-stack empty-state';
    elements.adminOperatorSnapshots.textContent = 'Sin lecturas operativas recientes.';
    return;
  }
  elements.adminOperatorSnapshots.className = 'list-stack';
  elements.adminOperatorSnapshots.innerHTML = '';
  items.forEach((row) => {
    const article = document.createElement('article');
    article.className = 'activity-item';
    const username = row.username ? `@${row.username}` : `Usuario ${row.user_id || '—'}`;
    const plan = planLabel(row.plan || 'none');
    const titleBits = [username, plan, String(resolveEffectiveTradingLabel(row) || 'inactive').toUpperCase()];
    const detailBits = [];
    if (row.exchange_balance !== null && row.exchange_balance !== undefined) detailBits.push(`Capital ${formatNumber(row.exchange_balance, 4)} USDT`);
    if (row.exchange_equity !== null && row.exchange_equity !== undefined) detailBits.push(`Equity ${formatNumber(row.exchange_equity, 4)} USDT`);
    if (row.last_symbol) detailBits.push(`Símbolo ${row.last_symbol}`);
    if (row.last_decision) detailBits.push(`Decisión ${row.last_decision}`);
    if (row.last_block_reason) detailBits.push(`Bloqueo ${row.last_block_reason}`);
    if (row.positions_count !== null && row.positions_count !== undefined) detailBits.push(`Posiciones ${row.positions_count}`);
    const message = row.runtime_message || 'Sin detalle operativo reciente.';
    article.innerHTML = `
      <div class="activity-dot ${pillClass(row.exchange_status || row.runtime_state || 'neutral')}"></div>
      <div class="activity-copy">
        <div class="activity-title">${titleBits.join(' · ')}</div>
        <div class="activity-detail">${message}${detailBits.length ? ` · ${detailBits.join(' · ')}` : ''}</div>
      </div>
      <div class="activity-time">${formatDate(row.runtime_checked_at || row.last_cycle_at)}</div>
    `;
    elements.adminOperatorSnapshots.appendChild(article);
  });
}

function renderAdmin(data) {
  state.admin = data;
  const visual = data.visual || {};
  const tradeStats = data.trade_stats_30d || {};
  const security = data.security || {};
  const monitor = data.monitor || {};
  const monitorCounts = monitor.counts || {};
  const systemRuntime = data.system_runtime || {};
  const operatorSnapshots = data.operator_snapshots || [];

  elements.adminVisualStats.innerHTML = '';
  elements.adminVisualStats.append(
    buildKpiCard('Usuarios', visual.total_users || 0, 'Registrados en esta DB.'),
    buildKpiCard('Trial vencido', visual.free_old || 0, 'Sin premium activo.'),
    buildKpiCard('Premium activo', visual.premium_active || 0, 'Estado actual.'),
    buildKpiCard('Premium vencido', visual.premium_expired || 0, 'Caducados.')
  );

  elements.adminSecurityStats.innerHTML = '';
  elements.adminSecurityStats.append(
    buildKpiCard('Keys cifradas', security.encrypted_private_keys || 0, 'Protegidas en reposo.'),
    buildKpiCard('Legacy plaintext', security.legacy_plaintext_private_keys || 0, 'Deuda crítica a rotar.'),
    buildKpiCard('Wallets', security.wallets_configured || 0, 'Usuarios con wallet configurada.')
  );

  elements.adminTradeStats.innerHTML = '';
  elements.adminTradeStats.append(
    buildKpiCard('Trades 30d', tradeStats.total || 0, 'Global del sistema.'),
    buildKpiCard('Wins', tradeStats.wins || 0, `Win rate ${tradeStats.win_rate ?? 0}%`),
    buildKpiCard('Losses', tradeStats.losses || 0, `Decisivas ${tradeStats.win_rate_decisive ?? 0}%`),
    buildKpiCard('Profit factor', tradeStats.profit_factor === Infinity ? '∞' : tradeStats.profit_factor ?? 0, `PnL ${tradeStats.pnl_total ?? 0}`),
    buildKpiCard('Gross profit', tradeStats.gross_profit ?? 0, 'Cierres positivos.'),
    buildKpiCard('Gross loss', tradeStats.gross_loss ?? 0, 'Pérdidas acumuladas.')
  );

  renderAdminActionHistory(
    elements.adminRecentActions,
    data.recent_actions || [],
    'Sin acciones administrativas registradas todavía.',
  );

  if (elements.adminMonitorStats) {
    elements.adminMonitorStats.innerHTML = '';
    elements.adminMonitorStats.append(
      buildKpiCard('Eventos', monitorCounts.events || 0, 'Feed global reciente.'),
      buildKpiCard('Aperturas/cierres', monitorCounts.trade_events || 0, 'Trading reciente del sistema.'),
      buildKpiCard('Pagos', monitorCounts.payment_events || 0, 'Confirmaciones recientes.'),
      buildKpiCard('Posiciones vivas', monitorCounts.active_positions || 0, 'Operaciones abiertas ahora mismo.')
    );
  }
  renderAdminMonitorFeed(monitor.events || []);
  renderAdminComponentTelemetry(systemRuntime);
  renderAdminOperatorSnapshots(operatorSnapshots);
  renderAdminLivePositions(monitor.active_positions || []);

  if (elements.adminBulkMigrationStatus) {
    elements.adminBulkMigrationStatus.textContent = `Legacy pendientes: ${security.legacy_plaintext_private_keys || 0} · Keys cifradas: ${security.encrypted_private_keys || 0}`;
  }
}

function formatDateTimeOrDash(value) {
  return value ? formatDate(value) : '—';
}

function renderAdminPlanPreview(preview) {
  if (!elements.adminPlanPreviewBox) return;
  if (!preview || !preview.ok) {
    elements.adminPlanPreviewBox.textContent = 'Sin previsualización todavía.';
    return;
  }

  const planCopy = `Plan destino: ${planLabel(preview.target_plan || preview.new_plan)}`;
  const baseCopy = preview.base_type === 'current_expiry'
    ? 'La extensión se sumará desde el vencimiento actual.'
    : 'La extensión empezará desde hoy porque no hay acceso vigente.';

  elements.adminPlanPreviewBox.innerHTML = `
    <strong>Previsualización antes de aplicar</strong><br />
    ${planCopy}. ${baseCopy}
    <div class="preview-grid">
      <div class="preview-item">
        <span class="preview-label">Plan actual</span>
        <strong>${planLabel(preview.previous_plan)}</strong>
      </div>
      <div class="preview-item">
        <span class="preview-label">Plan resultante</span>
        <strong>${planLabel(preview.new_plan)}</strong>
      </div>
      <div class="preview-item">
        <span class="preview-label">Días vigentes</span>
        <strong>${preview.previous_days_remaining || 0}</strong>
      </div>
      <div class="preview-item">
        <span class="preview-label">Días resultantes</span>
        <strong>${preview.new_days_remaining || 0}</strong>
      </div>
      <div class="preview-item">
        <span class="preview-label">Vencimiento actual</span>
        <strong>${formatDateTimeOrDash(preview.previous_expires_at)}</strong>
      </div>
      <div class="preview-item">
        <span class="preview-label">Nuevo vencimiento</span>
        <strong>${formatDateTimeOrDash(preview.new_expires_at)}</strong>
      </div>
    </div>
  `;
}

function updateAdminPlanGrantHint(user = state.adminSelectedUser) {
  if (!elements.adminPlanGrantHint) return;
  const targetPlan = elements.adminPlanSelect ? elements.adminPlanSelect.value : 'premium';
  if (!user || !user.user_id) {
    elements.adminPlanGrantHint.textContent = 'Carga un usuario para aplicar días exactos sin simular una compra automática.';
    return;
  }

  const planText = planLabel(targetPlan);
  let baseMessage = user.plan_active
    ? `El usuario tiene acceso vigente hasta ${formatDate(user.plan_expires_at)}. Los días nuevos se sumarán desde ese vencimiento.`
    : 'El usuario no tiene acceso vigente. Los días nuevos se aplicarán desde hoy.';

  if (targetPlan === 'trial' && user.plan === 'premium' && user.plan_active) {
    baseMessage = 'No se puede aplicar PRUEBA mientras el usuario tenga PREMIUM activo.';
  }

  elements.adminPlanGrantHint.textContent = `${baseMessage} Plan seleccionado: ${planText}. Esta acción es manual y no cuenta como compra automática.`;
}

async function refreshAdminPlanPreview() {
  const selected = state.adminSelectedUser;
  if (!selected || !selected.user_id || !elements.adminPlanDaysInput) {
    renderAdminPlanPreview(null);
    return;
  }
  const targetPlan = elements.adminPlanSelect ? elements.adminPlanSelect.value : 'premium';
  const rawDays = elements.adminPlanDaysInput.value.trim();
  const days = Number.parseInt(rawDays, 10);
  if (!Number.isInteger(days) || days <= 0) {
    renderAdminPlanPreview(null);
    return;
  }
  try {
    const payload = await apiFetch(`/api/v1/admin/users/${selected.user_id}/plan/manual-days-preview?plan=${encodeURIComponent(targetPlan)}&days=${days}`);
    renderAdminPlanPreview(payload.preview || null);
  } catch (error) {
    elements.adminPlanPreviewBox.textContent = error.message || 'No se pudo calcular la previsualización.';
  }
}

function buildAdminSearchResultItem(user) {
  const item = document.createElement('article');
  item.className = 'list-item';
  item.innerHTML = `
    <div class="list-item-header">
      <div>
        <div class="list-item-title">${user.username ? `@${user.username}` : `ID ${user.user_id}`}</div>
        <div class="list-item-meta">Plan ${user.plan || 'none'} · Estado ${resolveEffectiveTradingLabel(user)} · Solicitud ${resolveRequestedTradingStatus(user)} · Key ${user.private_key_storage || 'not_configured'} · Salud ${user.private_key_health || 'not_configured'}</div>
      </div>
      <button class="secondary-button" type="button" data-admin-user-id="${user.user_id}">Cargar</button>
    </div>
  `;
  const button = item.querySelector('button');
  button.addEventListener('click', async () => {
    await loadAdminUserDetail(user.user_id);
  });
  return item;
}

function renderAdminSelectedUser(user) {
  state.adminSelectedUser = user;
  elements.adminUserDetail.classList.remove('hidden');
  elements.adminUserTitle.textContent = user.username ? `@${user.username}` : `Usuario ${user.user_id}`;
  const remainingDays = Number(user.plan_days_remaining || 0);
  const expiryCopy = user.plan_active && user.plan_expires_at ? ` · Vence ${formatDate(user.plan_expires_at)} · ${remainingDays} día(s) restantes` : '';
  elements.adminUserSubtitle.textContent = `ID ${user.user_id} · Plan ${user.plan || 'none'}${expiryCopy} · Estado ${resolveEffectiveTradingLabel(user)} · Solicitud ${resolveRequestedTradingStatus(user)}`;
  updateAdminPlanGrantHint(user);
  if (elements.adminPlanSelect && !elements.adminPlanSelect.value) {
    elements.adminPlanSelect.value = 'premium';
  }
  if (elements.adminPlanDaysInput && !elements.adminPlanDaysInput.value) {
    elements.adminPlanDaysInput.value = '7';
  }
  void refreshAdminPlanPreview();
  elements.adminUserStats.innerHTML = '';
  elements.adminUserStats.append(
    buildKpiCard('Wallet', user.wallet_configured ? truncateMiddle(user.wallet || '—', 18) : 'Pendiente', 'Estado de wallet.'),
    buildKpiCard('Private key', user.private_key_configured ? 'Configurada' : 'Pendiente', user.private_key_storage || 'not_configured'),
    buildKpiCard('Salud credencial', user.private_key_health === 'invalid' ? 'Requiere reparación' : (user.private_key_configured ? 'Válida' : 'Pendiente'), user.private_key_runtime_error || (user.private_key_runtime_checked_at ? `Validada ${formatDate(user.private_key_runtime_checked_at)}` : 'Sin verificación runtime todavía.')),
    buildKpiCard('Plan', user.plan || 'none', user.plan_active ? `Vence ${formatDate(user.plan_expires_at)} · ${user.plan_days_remaining || 0} día(s)` : 'Sin acceso vigente.'),
    buildKpiCard('Políticas', user.terms_accepted ? 'Confirmadas' : 'Pendientes', user.terms_timestamp ? `Confirmadas ${formatDate(user.terms_timestamp)}` : 'Sin confirmación registrada.'),
    buildKpiCard('Solicitud trading', resolveRequestedTradingStatus(user), 'Estado pedido por el usuario o por soporte.'),
    buildKpiCard('Estado visible', resolveEffectiveTradingLabel(user), resolveEffectiveTradingDetail(user) || (user.last_open_at ? `Última apertura ${formatDate(user.last_open_at)}` : 'Sin aperturas registradas.')),
    buildKpiCard('Referidos válidos', user.referral_valid_count || 0, user.private_key_version ? `Cipher ${user.private_key_version}` : 'Sin versión de cifrado.')
  );

  if (elements.adminUserPerformance) {
    const perf = user.performance || {};
    elements.adminUserPerformance.innerHTML = '';
    elements.adminUserPerformance.append(
      buildAdminPerformanceCard('24h', perf['24h'] || {}),
      buildAdminPerformanceCard('7d', perf['7d'] || {}),
      buildAdminPerformanceCard('30d', perf['30d'] || {}),
    );
  }

  renderAdminActionHistory(
    elements.adminUserActionHistory,
    user.recent_admin_actions || [],
    'Sin acciones administrativas sobre este usuario todavía.',
  );

  if (elements.adminActivateTradingButton) {
    elements.adminActivateTradingButton.disabled = !user.terms_accepted || !user.wallet_configured || !user.private_key_configured || user.private_key_health === 'invalid' || isTradingEffectivelyActive(user);
  }
  if (elements.adminPauseTradingButton) {
    elements.adminPauseTradingButton.disabled = resolveRequestedTradingStatus(user) !== 'active';
  }
  if (elements.adminCloseTradingButton) {
    elements.adminCloseTradingButton.disabled = !Boolean(user.runtime_live_trade || user.runtime_active_symbol);
  }
  if (elements.adminMigrateKeyButton) {
    elements.adminMigrateKeyButton.disabled = !user.private_key_configured || user.private_key_storage === 'encrypted';
  }
  if (elements.adminResetCredentialsButton) {
    elements.adminResetCredentialsButton.disabled = !user.private_key_configured;
  }
}

function readCookie(name) {
  const item = document.cookie.split('; ').find((entry) => entry.startsWith(`${name}=`));
  return item ? decodeURIComponent(item.slice(name.length + 1)) : '';
}

async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set('Authorization', `Bearer ${state.token}`);
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const method = String(options.method || 'GET').toUpperCase();
  if (!state.token && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = readCookie('__Host-txh_csrf');
    if (csrf) headers.set('X-CSRF-Token', csrf);
  }

  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Error HTTP ${response.status}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

// Módulo gráfico: reutiliza la sesión ya validada por app.js sin exponer el Bearer.
window.hyperleagueMarketFetch = (path) => apiFetch(path);

function showWebLogin() {
  document.body.classList.add('auth-required');
  if (elements.webLoginScreen) elements.webLoginScreen.classList.remove('hidden');
}

function hideWebLogin() {
  document.body.classList.remove('auth-required');
  if (elements.webLoginScreen) elements.webLoginScreen.classList.add('hidden');
}

function setPreviewMode() {
  showWebLogin();
  setPill(elements.connectionBadge, 'Preview', 'preview');
  setStatus('Inicia sesión con teléfono y contraseña para conectar tu cuenta.', 'warning');
  setPill(elements.heroSessionPill, 'Preview', 'preview');
  setPill(elements.heroPlanPill, 'Plan preview', 'preview');
  setPill(elements.heroTradingPill, 'Trading preview', 'preview');
  elements.heroUser.textContent = 'Vista previa';
  elements.heroUserSubtext.textContent = 'Conecta desde Telegram para continuar';
  elements.heroWallet.textContent = '—';
  elements.heroWalletSubtext.textContent = 'Sin sesión';
  elements.heroKey.textContent = '—';
  elements.heroKeySubtext.textContent = 'Sin sesión';
  elements.heroAccess.textContent = 'Requiere sesión';
  elements.heroAccessSubtext.textContent = 'Autenticación de Telegram';
  setPill(elements.userPlanBadge, 'Preview', 'preview');
  setPill(elements.userReadinessBadge, 'Sin auth', 'blocked');
  setPill(elements.walletBadge, 'Sin auth', 'blocked');
  if (elements.sidebarUser) elements.sidebarUser.textContent = 'Vista previa';
  if (elements.sidebarPlan) elements.sidebarPlan.textContent = 'Preview';
  elements.userIdentity.textContent = 'Vista previa';
  elements.userIdentitySubtext.textContent = 'Abre desde Telegram para continuar';
  elements.userReadiness.textContent = 'Requiere sesión';
  elements.userTradingStatus.textContent = 'Conecta Telegram para operar';
  elements.walletConfigured.textContent = 'No disponible';
  elements.privateKeyConfigured.textContent = 'No disponible';
  elements.refreshButton.disabled = true;
  if (elements.systemTabButton) elements.systemTabButton.classList.add('hidden');
  if (elements.adminTabButton) elements.adminTabButton.classList.add('hidden');
  setSummarySystemVisibility(false);
  if (elements.accessStatsGrid) elements.accessStatsGrid.innerHTML = '';
  if (elements.performanceExecutiveGrid) elements.performanceExecutiveGrid.innerHTML = '';
  if (elements.systemRuntimeNotes) {
    elements.systemRuntimeNotes.className = 'readiness-list compact-list';
    elements.systemRuntimeNotes.innerHTML = '';
    elements.systemRuntimeNotes.appendChild(buildReadinessItem('Sin lectura del sistema', 'El estado operativo solo se resuelve con sesión real desde Telegram.', 'blocked'));
  }
}


// Autenticación web: teléfono y contraseña; el bot solo confirma contacto propio.
let phoneMode = 'login';
let phoneFlowId = '';
let phoneVerifyWs = null;
let phoneCompleting = false;

function phoneMessage(message, error = false) {
  if (elements.webLoginMessage) {
    elements.webLoginMessage.textContent = message || '';
    elements.webLoginMessage.classList.toggle('phone-auth-error', error);
  }
}

function flagForIso(iso) {
  return [...String(iso).toUpperCase()].map(c => String.fromCodePoint(127397 + c.charCodeAt(0))).join('');
}

function initPhoneAuth() {
  const country = $('phoneCountry');
  const filter = $('phoneCountryFilter');
  const dial = $('phoneDial');
  const data = Array.isArray(window.HL_PHONE_COUNTRIES) ? window.HL_PHONE_COUNTRIES : [];
  const drawCountries = () => {
    const selected = country.value || 'US';
    const term = (filter.value || '').trim().toLocaleLowerCase('es');
    country.replaceChildren();
    for (const item of data) {
      if (term && !`${item.name} ${item.dial} ${item.iso}`.toLocaleLowerCase('es').includes(term)) continue;
      const option = document.createElement('option');
      option.value = item.iso;
      option.textContent = `${flagForIso(item.iso)} ${item.name} (${item.dial})`;
      option.dataset.dial = item.dial;
      country.appendChild(option);
    }
    if (Array.from(country.options).some(item => item.value === selected)) country.value = selected;
    if (country.selectedOptions[0]) dial.textContent = country.selectedOptions[0].dataset.dial;
  };
  drawCountries();
  country.addEventListener('change', () => { if (country.selectedOptions[0]) dial.textContent = country.selectedOptions[0].dataset.dial; });
  filter.addEventListener('input', drawCountries);
  const tabs = {login: $('phoneLoginTab'), register: $('phoneRegisterTab')};
  const submit = $('phoneAuthSubmit');
  const pwd = $('phonePassword');
  const confirm = $('phoneConfirmPassword');
  const setMode = (mode) => {
    phoneMode = mode;
    tabs.login.classList.toggle('active', mode === 'login');
    tabs.register.classList.toggle('active', mode === 'register');
    tabs.login.setAttribute('aria-pressed', String(mode === 'login'));
    tabs.register.setAttribute('aria-pressed', String(mode === 'register'));
    $('phonePasswordConfirmBox').classList.toggle('hidden', mode !== 'register');
    pwd.autocomplete = mode === 'register' ? 'new-password' : 'current-password';
    confirm.required = mode === 'register';
    submit.textContent = mode === 'register' ? 'Crear cuenta y verificar' : 'Iniciar sesión';
    phoneMessage('');
  };
  tabs.login.addEventListener('click', () => {
    phoneFlowId = '';
    if (phoneVerifyWs) { phoneVerifyWs.close(); phoneVerifyWs = null; }
    sessionStorage.removeItem('hl_phone_flow');
    sessionStorage.removeItem('hl_phone_bot_url');
    $('phoneVerifyStep').classList.add('hidden');
    $('phoneAuthForm').classList.remove('hidden');
    setMode('login');
  });
  tabs.register.addEventListener('click', () => setMode('register'));
  for (const [field, button] of [['phonePassword','phoneRevealPassword'], ['phoneConfirmPassword','phoneRevealConfirm']]) {
    const input = $(field), eye = $(button);
    eye.addEventListener('click', () => {
      const shown = input.type === 'password';
      input.type = shown ? 'text' : 'password';
      eye.textContent = shown ? '◉̸' : '👁';
      eye.setAttribute('aria-label', shown ? 'Ocultar contraseña' : 'Mostrar contraseña');
      eye.setAttribute('aria-pressed', String(shown));
    });
    eye.textContent = '👁';
  }
  $('phoneAuthForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (phoneMode === 'register' && pwd.value !== confirm.value) {
      phoneMessage('Las contraseñas no coinciden.', true);
      return;
    }
    const dialValue = country.selectedOptions[0]?.dataset.dial;
    if (!dialValue) { phoneMessage('Selecciona un país válido.', true); return; }
    const national = $('phoneNumber').value.trim();
    const phone = national.startsWith('+') ? national : dialValue + national.replace(/[^\d]/g, '');
    submit.disabled = true;
    phoneMessage(phoneMode === 'register' ? 'Preparando verificación segura...' : 'Verificando credenciales...');
    try {
      const ref = new URLSearchParams(location.search).get('ref') || '';
      const response = await apiFetch('/api/v1/auth/phone/start', {
        method: 'POST', body: JSON.stringify({mode: phoneMode, phone, password: pwd.value, referrer: ref}),
      });
      if (response.authenticated) {
        sessionStorage.removeItem('hl_phone_flow');
        sessionStorage.removeItem('hl_phone_bot_url');
        pwd.value = '';
        confirm.value = '';
        window.location.assign('/');
        return;
      }
      phoneFlowId = response.flow_id;
      sessionStorage.setItem('hl_phone_flow', phoneFlowId);
      sessionStorage.setItem('hl_phone_bot_url', response.telegram_url);
      pwd.value = '';
      confirm.value = '';
      $('phoneAuthForm').classList.add('hidden');
      $('phoneVerifyStep').classList.remove('hidden');
      $('phoneBotLink').href = response.telegram_url;
      $('phoneVerifyState').textContent = 'Esperando que compartas tu contacto...';
      phoneMessage('Solicitud iniciada. Tienes 10 minutos para verificar.');
      connectPhoneVerification();
    } catch (error) {
      phoneMessage(error.message || 'No se pudo iniciar el registro.', true);
    } finally {
      submit.disabled = false;
    }
  });
  $('phoneVerifyManually').addEventListener('click', () => completePhoneVerification(true));
  $('phoneBackToForm').addEventListener('click', () => {
    if (phoneVerifyWs) phoneVerifyWs.close();
    phoneFlowId = '';
    sessionStorage.removeItem('hl_phone_flow');
    sessionStorage.removeItem('hl_phone_bot_url');
    $('phoneAuthForm').classList.remove('hidden');
    $('phoneVerifyStep').classList.add('hidden');
    phoneMessage('');
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && phoneFlowId) {
      void completePhoneVerification(false);
      if (!phoneVerifyWs || phoneVerifyWs.readyState > WebSocket.OPEN) connectPhoneVerification();
    }
  });
  // Las verificaciones guardadas solo corresponden a registros incompletos.
  // No abrir Telegram automáticamente al volver a iniciar sesión.
  sessionStorage.removeItem('hl_phone_flow');
  sessionStorage.removeItem('hl_phone_bot_url');
  phoneFlowId = '';
  $('phoneVerifyStep').classList.add('hidden');
  $('phoneAuthForm').classList.remove('hidden');
  setMode('login');
}

function connectPhoneVerification() {
  if (!phoneFlowId) return;
  if (phoneVerifyWs && phoneVerifyWs.readyState <= WebSocket.OPEN) return;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  phoneVerifyWs = new WebSocket(`${proto}//${location.host}/api/v1/auth/phone/updates/${encodeURIComponent(phoneFlowId)}`);
  phoneVerifyWs.addEventListener('message', (event) => {
    try {
      const message = JSON.parse(event.data);
      if (message.status === 'verified') void completePhoneVerification(false);
      if (message.status === 'ws_unavailable') $('phoneVerifyState').textContent = 'Puedes usar «Ya compartí mi contacto» para continuar.';
    } catch (error) { console.warn('Evento de verificación inválido'); }
  });
  phoneVerifyWs.addEventListener('close', () => {
    if (phoneFlowId) $('phoneVerifyState').textContent = 'Usa «Ya compartí mi contacto» si no avanza automáticamente.';
  });
}

async function completePhoneVerification(showError) {
  if (!phoneFlowId || phoneCompleting) return;
  phoneCompleting = true;
  try {
    await apiFetch('/api/v1/auth/phone/complete', {
      method: 'POST', body: JSON.stringify({flow_id: phoneFlowId}),
    });
    sessionStorage.removeItem('hl_phone_flow');
    sessionStorage.removeItem('hl_phone_bot_url');
    phoneFlowId = '';
    if (phoneVerifyWs) phoneVerifyWs.close();
    window.location.assign('/');
  } catch (error) {
    if (showError || !/Todavía falta verificar/.test(error.message || '')) {
      phoneMessage(error.message || 'Verificación pendiente.', showError);
    }
  } finally {
    phoneCompleting = false;
  }
}

async function authenticate() {
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  // Support existing Telegram Mini App until the browser migration is complete.
  if (tg && tg.initData) {
    state.telegram = tg;
    if (elements.logoutButton) elements.logoutButton.classList.add('hidden');
    tg.ready();
    tg.expand();
    tg.setHeaderColor('#09111f');
    tg.setBackgroundColor('#050913');
    const payload = await apiFetch('/api/v1/auth/telegram', {
      method: 'POST',
      body: JSON.stringify({ init_data: tg.initData }),
    });
    state.token = payload.access_token;
    state.isAdmin = Boolean(payload.is_admin);
  } else {
    // Browser: phone/password + bot Telegram verificado establecen una cookie segura.
    try {
      const payload = await apiFetch('/api/v1/auth/session');
      state.isAdmin = Boolean(payload.is_admin);
    } catch (error) {
      setPreviewMode();
      if (elements.webLoginMessage) {
        elements.webLoginMessage.textContent = 'Si ya tienes una cuenta verificada, inicia sesión con tu teléfono y contraseña. Telegram solo se utiliza durante el registro.';
      }
      return;
    }
  }

  state.authenticated = true;
  hideWebLogin();
  document.querySelectorAll('[data-tab="admin"]').forEach((item) => item.classList.toggle('hidden', !state.isAdmin));
  document.querySelectorAll('[data-tab="system"]').forEach((item) => item.classList.toggle('hidden', !state.isAdmin));
}

async function loadData(options = {}) {
  const showStatus = options.showStatus !== false;
  if (showStatus) setStatus('Sincronizando datos con el backend...', 'info');
  const [dashboard, control, performance, operations, referrals, systemRuntime, billing] = await Promise.all([
    apiFetch('/api/v1/dashboard?include_balance=false'),
    apiFetch('/api/v1/control'),
    apiFetch('/api/v1/performance'),
    apiFetch('/api/v1/operations?limit=20'),
    apiFetch('/api/v1/referrals'),
    apiFetch('/api/v1/system/runtime'),
    apiFetch('/api/v1/billing'),
  ]);

  renderDashboard(dashboard);
  renderControl(control);
  renderPerformance(performance);
  renderOperations(operations);
  renderReferrals(referrals);
  renderSystemRuntime(systemRuntime);
  renderBilling(billing);

  if (state.isAdmin) {
    document.querySelectorAll('[data-tab="admin"]').forEach((item) => item.classList.remove('hidden'));
    document.querySelectorAll('[data-tab="system"]').forEach((item) => item.classList.remove('hidden'));
    try {
      const admin = await apiFetch('/api/v1/admin/overview');
      renderAdmin(admin);
      setSummarySystemVisibility();
      if (admin && admin.partial) {
        setStatus('Admin cargado con incidencias parciales. Algunas métricas técnicas pueden estar incompletas.', 'warning');
      }
    } catch (error) {
      const fallbackAdmin = state.admin || {
        visual: {},
        trade_stats_30d: {},
        security: {},
        recent_actions: [],
        monitor: { events: [], active_positions: [], counts: { events: 0, active_positions: 0, trade_events: 0, payment_events: 0 } },
        partial: true,
      };
      renderAdmin(fallbackAdmin);
      setSummarySystemVisibility();
      setStatus(error?.message || 'El módulo admin tuvo una incidencia, pero la sesión sigue en modo administrador.', 'warning');
    }
  } else {
    document.querySelectorAll('[data-tab="admin"]').forEach((item) => item.classList.add('hidden'));
    document.querySelectorAll('[data-tab="system"]').forEach((item) => item.classList.add('hidden'));
    setSummarySystemVisibility(false);
    document.querySelectorAll('[data-panel="admin"]').forEach((panel) => panel.classList.remove('is-active'));
    document.querySelectorAll('[data-panel="system"]').forEach((panel) => panel.classList.remove('is-active'));
  }

  if (showStatus) setStatus('Plataforma web sincronizada correctamente.', 'success');
}

function bindTabs() {
  const buttons = Array.from(document.querySelectorAll('.tab'));
  const panels = Array.from(document.querySelectorAll('.panel'));
  const titleMap = {
    overview: 'Dashboard',
    system: 'Sistema',
    control: 'Control',
    demo: 'Cuenta DEMO',
    billing: 'Pagos',
    operations: 'Operaciones',
    admin: 'Admin',
  };
  const workspaceTitle = document.getElementById('workspaceTitle');
  buttons.forEach((button) => {
    button.addEventListener('click', () => {
      const target = button.dataset.tab;
      if ((target === 'admin' || target === 'system') && !state.isAdmin) return;
      buttons.forEach((item) => item.classList.toggle('is-active', item.dataset.tab === target));
      panels.forEach((panel) => panel.classList.toggle('is-active', panel.dataset.panel === target));
      if (workspaceTitle) workspaceTitle.textContent = titleMap[target] || 'Workspace';
      if (target === 'demo' && state.authenticated) void loadDemo();
      document.querySelector('.content-stack')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
}


function bindRefreshShortcuts() {
  elements.refreshButtonTop?.addEventListener('click', () => elements.refreshButton?.click());
}

async function saveConfiguration() {
  const wallet = elements.walletInput.value.trim();
  const privateKey = elements.privateKeyInput.value.trim();
  const payload = {};
  if (wallet) payload.wallet = wallet;
  if (privateKey) payload.private_key = privateKey;

  if (!payload.wallet && !payload.private_key) {
    setStatus('No hay cambios para guardar.', 'warning');
    return;
  }

  elements.saveConfigurationButton.disabled = true;
  try {
    const control = await apiFetch('/api/v1/control/configuration', {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
    renderControl(control);
    elements.privateKeyInput.value = '';
    setStatus('Configuración crítica actualizada correctamente.', 'success');
    await refreshSummaryOnly();
  } catch (error) {
    setStatus(error.message || 'No se pudo guardar la configuración.', 'error');
  } finally {
    elements.saveConfigurationButton.disabled = false;
  }
}

async function acceptTermsAction() {
  elements.acceptTermsButton.disabled = true;
  try {
    const control = await apiFetch('/api/v1/control/terms/accept', { method: 'POST' });
    renderControl(control);
    setStatus('Confirmación de políticas registrada correctamente.', 'success');
    await refreshSummaryOnly();
  } catch (error) {
    setStatus(error.message || 'No se pudo registrar la confirmación de políticas.', 'error');
    elements.acceptTermsButton.disabled = false;
  }
}

async function activateTradingAction() {
  elements.activateTradingButton.disabled = true;
  try {
    const payload = await apiFetch('/api/v1/control/trading/activate', { method: 'POST' });
    renderControl(payload.control);
    setStatus(payload.message || 'Trading activado.', 'success');
    await refreshSummaryOnly();
  } catch (error) {
    setStatus(error.message || 'No se pudo activar el trading.', 'error');
  } finally {
    elements.activateTradingButton.disabled = false;
  }
}

async function pauseTradingAction() {
  elements.pauseTradingButton.disabled = true;
  try {
    const payload = await apiFetch('/api/v1/control/trading/pause', { method: 'POST' });
    renderControl(payload.control);
    setStatus(payload.message || 'Trading pausado.', 'warning');
    await refreshSummaryOnly();
  } catch (error) {
    setStatus(error.message || 'No se pudo pausar el trading.', 'error');
  } finally {
    elements.pauseTradingButton.disabled = false;
  }
}


async function createPaymentOrderAction(days) {
  const planButtons = Array.from(document.querySelectorAll('.payment-plan-card .primary-button'));
  planButtons.forEach((button) => { button.disabled = true; });
  try {
    const payload = await apiFetch('/api/v1/billing/order', {
      method: 'POST',
      body: JSON.stringify({ days }),
    });
    renderBilling({ ...(state.billing || {}), active_order: payload.order });
    setStatus(`Orden premium ${days}d generada correctamente.`, 'success');
    const fresh = await apiFetch('/api/v1/billing');
    renderBilling(fresh);
  } catch (error) {
    setStatus(error.message || 'No se pudo generar la orden de pago.', 'error');
    const fresh = await apiFetch('/api/v1/billing').catch(() => null);
    if (fresh) renderBilling(fresh);
  } finally {
    planButtons.forEach((button) => { button.disabled = false; });
  }
}

async function confirmPaymentAction() {
  const order = state.billing?.active_order;
  if (!order?.order_id) {
    setStatus('No hay una orden activa para confirmar.', 'warning');
    return;
  }
  const originalConfirmText = elements.billingConfirmButton ? elements.billingConfirmButton.textContent : 'Confirmar pago';
  const originalCancelText = elements.billingCancelButton ? elements.billingCancelButton.textContent : 'Cancelar orden';
  if (elements.billingConfirmButton) {
    elements.billingConfirmButton.disabled = true;
    elements.billingConfirmButton.textContent = 'Verificando...';
  }
  if (elements.billingCancelButton) {
    elements.billingCancelButton.disabled = true;
    elements.billingCancelButton.textContent = 'Bloqueado';
  }
  try {
    const payload = await apiFetch('/api/v1/billing/order/confirm', {
      method: 'POST',
      body: JSON.stringify({ order_id: order.order_id }),
    });
    const isSuccess = Boolean(payload.ok);
    setStatus(payload.message || paymentReasonLabel(payload.reason), isSuccess ? 'success' : 'warning');
    const fresh = await apiFetch('/api/v1/billing');
    renderBilling(fresh);
    await refreshSummaryOnly();
  } catch (error) {
    setStatus(error.message || 'No se pudo confirmar el pago en este momento.', 'error');
    const fresh = await apiFetch('/api/v1/billing').catch(() => null);
    if (fresh) renderBilling(fresh);
  } finally {
    if (elements.billingConfirmButton) elements.billingConfirmButton.textContent = originalConfirmText;
    if (elements.billingCancelButton) elements.billingCancelButton.textContent = originalCancelText;
    syncBillingActionButtons();
  }
}

async function cancelPaymentAction() {
  const order = state.billing?.active_order;
  if (!order?.order_id) {
    setStatus('No hay una orden activa para cancelar.', 'warning');
    return;
  }
  const originalCancelText = elements.billingCancelButton ? elements.billingCancelButton.textContent : 'Cancelar orden';
  if (elements.billingCancelButton) {
    elements.billingCancelButton.disabled = true;
    elements.billingCancelButton.textContent = 'Cancelando...';
  }
  try {
    const payload = await apiFetch('/api/v1/billing/order/cancel', {
      method: 'POST',
      body: JSON.stringify({ order_id: order.order_id }),
    });
    setStatus(payload.message || 'Orden cancelada correctamente.', 'success');
    const fresh = await apiFetch('/api/v1/billing');
    renderBilling(fresh);
    await refreshSummaryOnly();
  } catch (error) {
    setStatus(error.message || 'No se pudo cancelar la orden en este momento.', 'error');
  } finally {
    if (elements.billingCancelButton) elements.billingCancelButton.textContent = originalCancelText;
    syncBillingActionButtons();
  }
}

async function refreshSummaryOnly() {
  const [dashboard, control, performance, systemRuntime, billing] = await Promise.all([
    apiFetch('/api/v1/dashboard?include_balance=false'),
    apiFetch('/api/v1/control'),
    apiFetch('/api/v1/performance'),
    apiFetch('/api/v1/system/runtime'),
    apiFetch('/api/v1/billing'),
  ]);
  renderDashboard(dashboard);
  renderControl(control);
  renderPerformance(performance);
  renderSystemRuntime(systemRuntime);
  renderBilling(billing);
}
async function loadAdminUserDetail(userId) {
  const detail = await apiFetch(`/api/v1/admin/users/${userId}`);
  renderAdminSelectedUser(detail);
}

async function searchAdminUsers() {
  const query = elements.adminSearchInput.value.trim();
  if (!query) {
    setStatus('Escribe un Telegram ID o username para buscar.', 'warning');
    return;
  }
  elements.adminSearchButton.disabled = true;
  try {
    const payload = await apiFetch(`/api/v1/admin/users/search?q=${encodeURIComponent(query)}&limit=10`);
    const items = Array.isArray(payload.items) ? payload.items : [];
    if (!items.length) {
      elements.adminSearchResults.className = 'list-stack empty-state';
      elements.adminSearchResults.textContent = 'No se encontraron usuarios con ese criterio.';
      elements.adminUserDetail.classList.add('hidden');
      state.adminSelectedUser = null;
      setStatus('Búsqueda admin sin resultados.', 'warning');
      return;
    }

    elements.adminSearchResults.className = 'list-stack';
    elements.adminSearchResults.innerHTML = '';
    items.forEach((user) => elements.adminSearchResults.appendChild(buildAdminSearchResultItem(user)));
    setStatus(`Se encontraron ${items.length} usuarios.`, 'success');
  } catch (error) {
    setStatus(error.message || 'No se pudo buscar el usuario.', 'error');
  } finally {
    elements.adminSearchButton.disabled = false;
  }
}

async function grantManualPremiumDaysForSelectedUser() {
  const selected = state.adminSelectedUser;
  if (!selected || !selected.user_id) {
    setStatus('Primero carga un usuario admin.', 'warning');
    return;
  }

  const targetPlan = elements.adminPlanSelect ? elements.adminPlanSelect.value : 'premium';
  const rawDays = elements.adminPlanDaysInput ? elements.adminPlanDaysInput.value.trim() : '';
  const days = Number.parseInt(rawDays, 10);
  if (!Number.isInteger(days) || days <= 0) {
    setStatus('Introduce una cantidad válida de días mayor que cero.', 'warning');
    return;
  }

  const reason = getAdminActionReason();
  let preview = null;
  try {
    const previewPayload = await apiFetch(`/api/v1/admin/users/${selected.user_id}/plan/manual-days-preview?plan=${encodeURIComponent(targetPlan)}&days=${days}`);
    preview = previewPayload.preview || null;
    renderAdminPlanPreview(preview);
  } catch (error) {
    setStatus(error.message || 'No se pudo calcular la previsualización.', 'error');
    return;
  }

  const confirmed = await requestStrongConfirmation(
    'Confirmación reforzada · Extensión manual de plan',
    [
      `Usuario: ${selected.username ? '@' + selected.username : 'ID ' + selected.user_id}`,
      `Plan actual: ${planLabel(preview?.previous_plan)}`,
      `Plan resultante: ${planLabel(preview?.new_plan || targetPlan)}`,
      `Días a agregar: ${days}`,
      `Nuevo vencimiento: ${formatDateTimeOrDash(preview?.new_expires_at)}`,
      reason ? `Motivo: ${reason}` : 'Motivo: sin nota administrativa',
    ],
  );
  if (!confirmed) return;

  elements.adminGrantPlanButton.disabled = true;
  try {
    const payload = await apiFetch(`/api/v1/admin/users/${selected.user_id}/plan/manual-days`, {
      method: 'POST',
      body: JSON.stringify({ plan: targetPlan, days, reason }),
    });
    renderAdminSelectedUser(payload.user);
    await loadData();
    setStatus(payload.message || `${planLabel(targetPlan)} actualizado por ${days} días.`, 'success');
  } catch (error) {
    setStatus(error.message || 'No se pudo aplicar la extensión manual.', 'error');
  } finally {
    elements.adminGrantPlanButton.disabled = false;
  }
}

async function runAdminSelectedAction(button, path, fallbackMessage, variant = 'success', confirmationTitle = 'Confirmación reforzada', confirmationDetails = []) {
  const selected = state.adminSelectedUser;
  if (!selected || !selected.user_id) {
    setStatus('Primero carga un usuario admin.', 'warning');
    return;
  }

  const reason = getAdminActionReason();
  const confirmed = await requestStrongConfirmation(confirmationTitle, [
    `Usuario: ${selected.username ? '@' + selected.username : 'ID ' + selected.user_id}`,
    ...confirmationDetails,
    reason ? `Motivo: ${reason}` : 'Motivo: sin nota administrativa',
  ]);
  if (!confirmed) return;

  if (button) button.disabled = true;
  try {
    const payload = await apiFetch(path(selected.user_id), {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });
    if (payload.user) {
      renderAdminSelectedUser(payload.user);
    }
    await loadData();
    setStatus(payload.message || fallbackMessage, variant);
  } catch (error) {
    setStatus(error.message || fallbackMessage, 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

async function activateTradingForSelectedUser() {
  await runAdminSelectedAction(
    elements.adminActivateTradingButton,
    (userId) => `/api/v1/admin/users/${userId}/trading/activate`,
    'No se pudo activar trading para el usuario.',
    'success',
    'Confirmación reforzada · Reanudar trading',
    ['Se intentará reactivar el motor del usuario.'],
  );
}

async function pauseTradingForSelectedUser() {
  await runAdminSelectedAction(
    elements.adminPauseTradingButton,
    (userId) => `/api/v1/admin/users/${userId}/trading/pause`,
    'No se pudo pausar trading para el usuario.',
    'warning',
    'Confirmación reforzada · Pausar trading',
    ['Esta acción detiene la operativa del usuario hasta nuevo cambio manual.'],
  );
}

async function closeTradingForSelectedUser() {
  const selected = state.adminSelectedUser;
  const symbol = selected?.runtime_active_symbol ? `Símbolo activo: ${selected.runtime_active_symbol}` : 'Se intentará cerrar la posición activa detectada en exchange.';
  await runAdminSelectedAction(
    elements.adminCloseTradingButton,
    (userId) => `/api/v1/admin/users/${userId}/trading/close`,
    'No se pudo cerrar la operación del usuario.',
    'warning',
    'Confirmación reforzada · Cerrar trading',
    [
      symbol,
      'Esta acción fuerza el cierre reduce-only de la posición abierta y deja nuevas entradas pausadas.',
      'Úsala solo para incidencias o intervención manual de soporte.',
    ],
  );
}

async function migrateKeyForSelectedUser() {
  await runAdminSelectedAction(
    elements.adminMigrateKeyButton,
    (userId) => `/api/v1/admin/users/${userId}/security/migrate-key`,
    'No se pudo migrar la private key del usuario.',
    'success',
    'Confirmación reforzada · Migrar private key legacy',
    ['Se intentará convertir la key almacenada a cifrado en reposo.'],
  );
}

async function resetStatsForSelectedUser() {
  await runAdminSelectedAction(
    elements.adminResetStatsButton,
    (userId) => `/api/v1/admin/users/${userId}/stats/reset`,
    'No se pudo resetear el rendimiento del usuario.',
    'warning',
    'Confirmación reforzada · Resetear rendimiento',
    ['Esta acción reinicia el punto de partida de métricas del usuario.'],
  );
}

async function resetCredentialsForSelectedUser() {
  await runAdminSelectedAction(
    elements.adminResetCredentialsButton,
    (userId) => `/api/v1/admin/users/${userId}/security/reset-credentials`,
    'No se pudo resetear la credencial del usuario.',
    'warning',
    'Confirmación reforzada · Resetear credencial operativa',
    [
      'Se borrará la private key almacenada del usuario.',
      'La wallet pública se conservará para facilitar la reconfiguración.',
      'El trading quedará pausado hasta que el usuario vuelva a configurar su key.',
    ],
  );
}

async function clearAdminMonitorFeed() {
  if (!elements.adminClearMonitorButton) return;
  const confirmed = await requestStrongConfirmation(
    'Limpiar feed operativo',
    [
      'Esto ocultará del monitor admin los eventos ya revisados.',
      'La actividad del usuario no se elimina.',
      'Las posiciones vivas no se tocan.',
    ],
  );
  if (!confirmed) return;

  elements.adminClearMonitorButton.disabled = true;
  if (elements.adminClearMonitorStatus) {
    elements.adminClearMonitorStatus.textContent = 'Limpiando feed operativo...';
  }
  try {
    const payload = await apiFetch('/api/v1/admin/monitor/clear', {
      method: 'POST',
      body: JSON.stringify({ reason: 'Limpieza manual del feed operativo' }),
    });
    if (elements.adminClearMonitorStatus) {
      elements.adminClearMonitorStatus.textContent = payload.message || 'Feed operativo limpiado.';
    }
    setStatus(payload.message || 'Feed operativo limpiado.', 'success');
    if (state.isAdmin) {
      const admin = await apiFetch('/api/v1/admin/overview');
      renderAdmin(admin || {});
    }
  } catch (error) {
    if (elements.adminClearMonitorStatus) {
      elements.adminClearMonitorStatus.textContent = error.message || 'No se pudo limpiar el feed operativo.';
    }
    setStatus(error.message || 'No se pudo limpiar el feed operativo.', 'error');
  } finally {
    elements.adminClearMonitorButton.disabled = false;
  }
}

async function bulkMigrateLegacyKeys() {
  if (!elements.adminBulkMigrateButton) return;
  const reason = getAdminActionReason();
  const confirmed = await requestStrongConfirmation(
    'Confirmación reforzada · Migración masiva de keys legacy',
    [
      'Se migrarán hasta 25 registros legacy en esta ejecución.',
      reason ? `Motivo: ${reason}` : 'Motivo: sin nota administrativa',
    ],
  );
  if (!confirmed) return;

  elements.adminBulkMigrateButton.disabled = true;
  try {
    const payload = await apiFetch('/api/v1/admin/security/migrate-legacy-keys?limit=25', {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });
    if (elements.adminBulkMigrationStatus) {
      elements.adminBulkMigrationStatus.textContent = payload.message || `Migradas ${payload.migrated_count || 0} keys.`;
    }
    await loadData();
    setStatus(payload.message || 'Migración legacy completada.', 'success');
  } catch (error) {
    setStatus(error.message || 'No se pudo ejecutar la migración legacy.', 'error');
  } finally {
    elements.adminBulkMigrateButton.disabled = false;
  }
}

function demoCash(value, digits = 2) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(digits)} USDC` : '—';
}

function demoMetric(label, value) {
  return `<div class="demo-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`;
}

function renderDemoPosition(d) {
  const p = d.position;
  if (p) {
    elements.demoPosition.className = 'demo-position';
    elements.demoPosition.innerHTML = [
      demoMetric('Par', p.symbol), demoMetric('Dirección', p.direction.toUpperCase()),
      demoMetric('Estrategia', p.strategy_id || '—'), demoMetric('Régimen', p.regime_id || '—'),
      demoMetric('Entrada', formatNumber(p.entry_price, 6)), demoMetric('Precio observado', formatNumber(p.mark_price, 6)),
      demoMetric('Stop Loss', formatNumber(p.stop_price, 6)), demoMetric('Take Profit', formatNumber(p.target_price, 6)),
      demoMetric('Margen / Apalancamiento', `${demoCash(p.margin_usdc)} · ${p.leverage}x`),
      demoMetric('PnL flotante (ledger)', demoCash(p.unrealized_pnl_usdc)),
      demoMetric('Movimiento de precio', Number(p.entry_price) > 0 && Number(p.mark_price) > 0
        ? `${((p.direction === 'short' ? -1 : 1) * (Number(p.mark_price) / Number(p.entry_price) - 1) * 100).toFixed(3)}%` : '—'),
      demoMetric('ROE estimado sobre margen', Number(p.margin_usdc) > 0
        ? `${(100 * Number(p.unrealized_pnl_usdc || 0) / Number(p.margin_usdc)).toFixed(3)}%` : '—'),
      demoMetric('Comisión apertura', demoCash(p.entry_fee_usdc, 5)),
      demoMetric('Cantidad', formatNumber(p.quantity, 8)),
      demoMetric('Apertura', formatDate(p.opened_at)),
    ].join('');
    elements.demoPosition.appendChild(tradeShareButton('demo-open', p, 'Compartir operación DEMO'));
  } else {
    elements.demoPosition.className = 'empty-state';
    elements.demoPosition.textContent = 'Sin posición DEMO. El bot buscará una señal aprobada por el motor.';
  }
}

function renderDemo() {
  const d = state.demo;
  if (!d) return;
  setPill(elements.demoStatusBadge, d.enabled ? 'Bot DEMO activo' : 'Bot DEMO pausado', d.enabled ? 'active' : 'warning');
  elements.demoEquity.textContent = demoCash(d.equity_usdc);
  elements.demoPnl.textContent = `PnL realizado: ${demoCash(d.realized_pnl_usdc)} · Flotante: ${demoCash(d.unrealized_pnl_usdc)}`;
  if (elements.demoCapitalInput && document.activeElement !== elements.demoCapitalInput) {
    elements.demoCapitalInput.value = Number(d.initial_equity_usdc ?? 100).toFixed(2);
  }
  const canChangeCapital = !d.enabled && !d.position && !d.position_open
    && Number(d.trades_closed || 0) === 0 && !(d.history || []).length;
  if (elements.demoCapitalSave) elements.demoCapitalSave.disabled = !canChangeCapital;
  if (elements.demoCapitalInput) elements.demoCapitalInput.disabled = !canChangeCapital;
  if (elements.demoCapitalHint) elements.demoCapitalHint.textContent = canChangeCapital
    ? 'Saldo simulado individual. Puedes modificarlo antes de la primera operación.'
    : d.enabled ? 'Pausa DEMO antes de configurar el capital.'
    : d.position ? 'Espera al cierre de la posición antes de configurar capital.'
    : 'Ya hay operaciones registradas. Usa Reiniciar saldo para borrarlas si quieres comenzar de cero con otro capital.';
  elements.demoStats.innerHTML = [
    demoMetric('Capital inicial', demoCash(d.initial_equity_usdc)),
    demoMetric('Saldo realizado', demoCash(d.balance_usdc)),
    demoMetric('Margen libre', demoCash(d.available_usdc)),
    demoMetric('Margen reservado', demoCash(d.locked_margin_usdc)),
    demoMetric('Operaciones cerradas', Number(d.trades_closed || 0)),
    demoMetric('Ganadas / perdidas', `${d.wins || 0} / ${d.losses || 0}`),
    demoMetric('Comisión taker', `${formatNumber(d.taker_fee_bps, 1)} bps`),
  ].join('');
  elements.demoStartButton.disabled = Boolean(d.enabled || d.credentials_ready !== true);
  elements.demoPauseButton.disabled = !d.enabled;
  elements.demoResetButton.disabled = Boolean(d.enabled || d.position);
  elements.demoNotice.textContent = d.credentials_ready !== true
    ? 'Configura tu wallet y una private key cifrada válida en Control antes de activar DEMO. La clave NO firma órdenes simuladas.'
    : d.enabled
    ? 'Bot DEMO activo · Precios en vivo por WebSocket de Hyperliquid · SL/TP por eventos.'
    : d.position ? 'Entradas pausadas. La posición abierta sigue siendo gestionada por SL/TP.'
    : 'Bot DEMO pausado. Ya puedes activarlo; los fondos son simulados.';
  elements.demoNotice.className = `status-banner ${d.credentials_ready !== true ? 'warning' : d.enabled ? 'success' : 'info'}`;
  renderDemoPosition(d);
  const history = Array.isArray(d.history) ? d.history : [];
  elements.demoHistoryBadge.textContent = String(d.trades_closed || 0);
  elements.demoHistory.className = history.length ? 'demo-history' : 'empty-state';
  elements.demoHistory.innerHTML = history.length ? history.slice(0, 40).map((t) => `
    <div class="demo-history-row">
      <div><strong>${escapeHtml(t.symbol || '')} · ${escapeHtml(String(t.direction || '').toUpperCase())}</strong>
      <small>${escapeHtml(t.strategy_id || '—')} · ${escapeHtml(formatDate(t.closed_at))} · ${escapeHtml(t.close_reason || '—')}</small></div>
      <div class="demo-history-actions"><strong class="${Number(t.pnl_net_usdc || 0) >= 0 ? 'demo-positive' : 'demo-negative'}">${escapeHtml(demoCash(t.pnl_net_usdc))}</strong><button type="button" class="secondary-button trade-share-trigger" data-share-demo-index="${history.indexOf(t)}">Compartir</button></div>
    </div>`).join('') : 'Todavía no hay operaciones simuladas cerradas.';
}

async function loadDemo() {
  if (!state.authenticated || state.demoLoading) return;
  state.demoLoading = true;
  try {
    state.demo = await apiFetch('/api/v1/demo');
    renderDemo();
  } catch (error) {
    elements.demoNotice.textContent = error.message || 'Servicio DEMO no disponible.';
    elements.demoNotice.className = 'status-banner warning';
    elements.demoStartButton.disabled = true;
    elements.demoPauseButton.disabled = true;
    elements.demoResetButton.disabled = true;
    if (elements.demoCapitalSave) elements.demoCapitalSave.disabled = true;
    if (elements.demoCapitalInput) elements.demoCapitalInput.disabled = true;
  } finally {
    state.demoLoading = false;
  }
}

async function mutateDemo(path, confirmation = false) {
  if (confirmation && !window.confirm('¿Reiniciar definitivamente el saldo y el historial de operaciones DEMO?')) return;
  const controls = [elements.demoStartButton, elements.demoPauseButton, elements.demoResetButton];
  controls.forEach((c) => { c.disabled = true; });
  try {
    state.demo = await apiFetch(path, { method: 'POST' });
    renderDemo();
    await loadDemo();
  } catch (error) {
    await loadDemo();
    elements.demoNotice.textContent = error.message || 'No pudo completarse la operación.';
    elements.demoNotice.className = 'status-banner warning';
  }
}

async function saveDemoCapital(event) {
  event.preventDefault();
  if (!elements.demoCapitalInput || !elements.demoCapitalSave) return;
  const value = Number(elements.demoCapitalInput.value);
  if (!Number.isFinite(value) || value < 10 || value > 100000 || Math.round(value * 100) / 100 !== value) {
    elements.demoNotice.textContent = 'Escribe un capital de 10 a 100000 USDC, máximo dos decimales.';
    elements.demoNotice.className = 'status-banner warning';
    return;
  }
  elements.demoCapitalSave.disabled = true;
  try {
    state.demo = await apiFetch('/api/v1/demo/capital', {
      method: 'POST', body: JSON.stringify({ capital_usdc: value }),
    });
    // /capital es una mutación protegida con sesión y CAS. Actualizamos datos con GET.
    await loadDemo();
    elements.demoNotice.textContent = `Capital inicial DEMO configurado: ${demoCash(value)}.`;
    elements.demoNotice.className = 'status-banner success';
  } catch (error) {
    await loadDemo();
    elements.demoNotice.textContent = error.message || 'No fue posible actualizar el capital DEMO.';
    elements.demoNotice.className = 'status-banner warning';
  }
}

// El precio del libro público no se almacena como operación ni autoriza un
// cierre: el navegador solo proyecta PnL hasta recibir ledger DEMO firmado por
// el servidor. Los cierres dependen exclusivamente del worker.
window.addEventListener('hl:market-quote', (event) => {
  const data = event.detail;
  const bid = Number(data?.bid), ask = Number(data?.ask);
  const symbol = String(data?.symbol || '').toUpperCase();
  if (symbol && Number.isFinite(bid) && bid > 0 && Number.isFinite(ask) && ask >= bid) {
    shareQuotes.set(symbol, { bid, ask, receivedAt: Date.now() });
    // Mantener acotado el mapa de cotizaciones aunque se exploren muchos pares.
    if (shareQuotes.size > 60) shareQuotes.delete(shareQuotes.keys().next().value);
  }
  const account = state.demo;
  const p = account?.position;
  if (!p || !data || String(p.symbol).toUpperCase() !== String(data.symbol).toUpperCase()) return;
  const entry = Number(p.entry_price), qty = Number(p.quantity);
  if (![entry, qty, bid, ask].every(n => Number.isFinite(n) && n > 0) || bid > ask) return;
  const long = String(p.direction).toLowerCase() === 'long';
  const feeRate = Number(account.taker_fee_bps || 0) / 10000;
  const slipRate = Number(account.slippage_bps || 0) / 10000;
  const exitPrice = long ? bid * (1 - slipRate) : ask * (1 + slipRate);
  const gross = (long ? 1 : -1) * (exitPrice - entry) * qty;
  const net = gross - exitPrice * qty * feeRate;
  p.mark_price = (bid + ask) / 2;
  p.unrealized_pnl_usdc = net;  // excluye entry fee, ya descontada del balance
  account.unrealized_pnl_usdc = net;
  account.equity_usdc = Number(account.balance_usdc || 0) + net;
  elements.demoEquity.textContent = demoCash(account.equity_usdc);
  elements.demoPnl.textContent = `PnL realizado: ${demoCash(account.realized_pnl_usdc)} · Flotante estimado: ${demoCash(net)}`;
  renderDemoPosition(account);
});

// Un único socket privado web por sesión; todos los paneles reciben eventos.
// Sin polling de saldos, posiciones ni operaciones. Una reconexión rehidrata
// el snapshot desde el servidor; el botón Actualizar sigue siendo manual.
let privateWs = null;
let privateWsReconnect = 0;
let privateWsTimer = null;

function startRealtimeStream() {
  if (!state.authenticated || privateWs) return;
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const endpoint = `${protocol}//${location.host}/api/v1/stream`;
  let socket;
  try { socket = new WebSocket(endpoint); } catch { return; }
  privateWs = socket;
  socket.onopen = () => { privateWsReconnect = 0; };
  socket.onmessage = (message) => {
    let packet;
    try { packet = JSON.parse(message.data); } catch { return; }
    if (!packet || typeof packet !== 'object') return;
    if (packet.type === 'demo') {
      state.demo = { ...(state.demo || {}), ...packet.data };
      renderDemo();
    } else if (packet.type === 'profile') {
      // Nunca insertar private key en DOM; solo el perfil público.
      state.realtimeProfile = packet.data;
      if (state.demo) {
        state.demo.credentials_ready = Boolean(packet.data?.wallet_configured && packet.data?.private_key_configured
          && packet.data?.private_key_storage === 'encrypted' && !packet.data?.credential_repair_required);
        renderDemo();
      }
    } else if (packet.type === 'dashboard') {
      renderDashboard(packet.data);
    } else if (packet.type === 'control') {
      renderControl(packet.data);
    } else if (packet.type === 'performance') {
      renderPerformance(packet.data);
    } else if (packet.type === 'operations') {
      renderOperations(packet.data);
    } else if (packet.type === 'error') {
      elements.demoNotice.textContent = packet.message || 'Canal en tiempo real no disponible.';
      elements.demoNotice.className = 'status-banner warning';
    }
    window.dispatchEvent(new CustomEvent('hl:realtime', { detail: packet }));
  };
  socket.onclose = () => {
    if (privateWs !== socket) return;
    privateWs = null;
    if (!state.authenticated) return;
    elements.demoNotice.textContent = 'Sincronización WebSocket desconectada. Datos de cuenta no actualizados.';
    elements.demoNotice.className = 'status-banner warning';
    const backoff = Math.min(30_000, 1000 * (2 ** Math.min(privateWsReconnect++, 5)));
    privateWsTimer = setTimeout(startRealtimeStream, backoff);
  };
  socket.onerror = () => { /* onclose gestiona reconexión segura */ };
}

function closeRealtimeStream() {
  clearTimeout(privateWsTimer);
  const socket = privateWs;
  privateWs = null;
  if (socket) { socket.onclose = null; socket.close(); }
}

function bindActions() {
  elements.demoStartButton?.addEventListener('click', () => mutateDemo('/api/v1/demo/start'));
  elements.demoPauseButton?.addEventListener('click', () => mutateDemo('/api/v1/demo/pause'));
  elements.demoResetButton?.addEventListener('click', () => mutateDemo('/api/v1/demo/reset', true));
  elements.demoCapitalForm?.addEventListener('submit', saveDemoCapital);
  elements.refreshButton.addEventListener('click', async () => {
    elements.refreshButton.disabled = true;
  if (elements.systemTabButton) elements.systemTabButton.classList.add('hidden');
  if (elements.adminTabButton) elements.adminTabButton.classList.add('hidden');
  setSummarySystemVisibility(false);
    try {
      await loadData();
    } catch (error) {
      setStatus(error.message || 'No se pudieron refrescar los datos.', 'error');
    } finally {
      elements.refreshButton.disabled = false;
    }
  });

  elements.configurationForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    await saveConfiguration();
  });

  elements.clearConfigurationButton.addEventListener('click', () => {
    elements.walletInput.value = '';
    elements.privateKeyInput.value = '';
  });

  elements.acceptTermsButton.addEventListener('click', acceptTermsAction);
  elements.activateTradingButton.addEventListener('click', activateTradingAction);
  elements.pauseTradingButton.addEventListener('click', pauseTradingAction);
  if (elements.billingConfirmButton) elements.billingConfirmButton.addEventListener('click', confirmPaymentAction);
  if (elements.billingCancelButton) elements.billingCancelButton.addEventListener('click', cancelPaymentAction);

  document.addEventListener('click', async (event) => {
    const shareButton = event.target.closest('[data-action="share-referral"]');
    if (shareButton) {
      event.preventDefault();
      shareReferralLink();
      return;
    }
    const button = event.target.closest('[data-copy-text]');
    if (!button) return;
    event.preventDefault();
    await copyTextToClipboard(
      button.getAttribute('data-copy-text'),
      button.getAttribute('data-copy-success') || 'Copiado correctamente.',
    );
  });

  if (elements.adminSearchForm) {
    elements.adminSearchForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      await searchAdminUsers();
    });
  }

  if (elements.adminGrantPlanButton) {
    elements.adminGrantPlanButton.addEventListener('click', grantManualPremiumDaysForSelectedUser);
  }
  if (elements.adminPlanSelect) {
    elements.adminPlanSelect.addEventListener('change', () => {
      updateAdminPlanGrantHint();
      void refreshAdminPlanPreview();
    });
  }
  if (elements.adminPlanDaysInput) {
    elements.adminPlanDaysInput.addEventListener('input', () => { void refreshAdminPlanPreview(); });
    elements.adminPlanDaysInput.addEventListener('change', () => { void refreshAdminPlanPreview(); });
  }
  if (elements.adminActivateTradingButton) {
    elements.adminActivateTradingButton.addEventListener('click', activateTradingForSelectedUser);
  }
  if (elements.adminPauseTradingButton) {
    elements.adminPauseTradingButton.addEventListener('click', pauseTradingForSelectedUser);
  }
  if (elements.adminCloseTradingButton) {
    elements.adminCloseTradingButton.addEventListener('click', closeTradingForSelectedUser);
  }
  if (elements.adminMigrateKeyButton) {
    elements.adminMigrateKeyButton.addEventListener('click', migrateKeyForSelectedUser);
  }
  if (elements.adminResetCredentialsButton) {
    elements.adminResetCredentialsButton.addEventListener('click', resetCredentialsForSelectedUser);
  }
  if (elements.adminResetStatsButton) {
    elements.adminResetStatsButton.addEventListener('click', resetStatsForSelectedUser);
  }
  if (elements.adminBulkMigrateButton) {
    elements.adminBulkMigrateButton.addEventListener('click', bulkMigrateLegacyKeys);
  }
  if (elements.adminClearMonitorButton) {
    elements.adminClearMonitorButton.addEventListener('click', clearAdminMonitorFeed);
  }
}

async function bootstrap() {
  bindTabs();
  initPhoneAuth();
  bindRefreshShortcuts();
  bindActions();
  if (elements.logoutButton) {
    elements.logoutButton.addEventListener('click', async () => {
      try { await apiFetch('/api/v1/auth/logout', { method: 'POST' }); }
      catch (error) { console.error('Logout response', error); }
      closeRealtimeStream();
      state.token = null;
      state.authenticated = false;
      window.location.assign('/');
    });
  }

  try {
    await authenticate();
    if (!state.authenticated) return;
    await loadData();
    if (document.querySelector('[data-panel="demo"]')?.classList.contains('is-active')) await loadDemo();
    startRealtimeStream();
  } catch (error) {
    console.error(error);
    if (state.token) {
      setStatus(error.message || 'No se pudo iniciar la MiniApp.', 'error');
    }
  }
}

bootstrap();

// Los historiales se vuelven a dibujar al recibir eventos WebSocket; el listener
// delegado permanece instalado y siempre lee la cuenta DEMO actual.
if (elements.demoHistory) {
  elements.demoHistory.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-share-demo-index]');
    if (!button || !elements.demoHistory.contains(button)) return;
    const history = state.demo?.history;
    const index = Number(button.dataset.shareDemoIndex);
    if (Number.isInteger(index) && index >= 0 && index < history?.length) {
      shareTrade('demo-closed', history[index]);
    }
  });
}
