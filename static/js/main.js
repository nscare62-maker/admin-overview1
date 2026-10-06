/**
 * S2C Admin Dashboard - Core Client Interactions
 */

document.addEventListener('DOMContentLoaded', function () {
    // -------------------------------------------------------------
    // 1. Mobile Sidebar Navigation Drawer
    // -------------------------------------------------------------
    const sidebar = document.getElementById('appSidebar');
    const backdrop = document.getElementById('sidebarBackdrop');
    const toggleBtn = document.getElementById('sidebarToggleBtn');
    const closeBtn = document.getElementById('sidebarCloseBtn');

    function openSidebar() {
        if (sidebar) sidebar.classList.add('show');
        if (backdrop) backdrop.classList.add('show');
        document.body.style.overflow = 'hidden';
    }

    function closeSidebar() {
        if (sidebar) sidebar.classList.remove('show');
        if (backdrop) backdrop.classList.remove('show');
        document.body.style.overflow = '';
    }

    if (toggleBtn) {
        toggleBtn.addEventListener('click', openSidebar);
    }
    if (closeBtn) {
        closeBtn.addEventListener('click', closeSidebar);
    }
    if (backdrop) {
        backdrop.addEventListener('click', closeSidebar);
    }

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && sidebar && sidebar.classList.contains('show')) {
            closeSidebar();
        }
    });

    // -------------------------------------------------------------
    // 2. Clickable Table Rows with Keyboard Accessibility
    // -------------------------------------------------------------
    document.querySelectorAll('.clickable-row').forEach(row => {
        row.addEventListener('click', function (e) {
            // Prevent trigger if clicking on an explicit link, button, or form inside row
            if (e.target.closest('a, button, input, form, select')) {
                return;
            }
            const href = this.dataset.href;
            if (href) {
                window.location.href = href;
            }
        });

        row.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') {
                if (e.target.closest('a, button, input, form, select')) {
                    return;
                }
                e.preventDefault();
                const href = this.dataset.href;
                if (href) {
                    window.location.href = href;
                }
            }
        });
    });

    // -------------------------------------------------------------
    // 3. Confirm Before Critical Actions (Rejections)
    // -------------------------------------------------------------
    document.querySelectorAll('form[action*="reject"]').forEach(form => {
        form.addEventListener('submit', function (e) {
            if (!confirm('Are you sure you want to reject this request?')) {
                e.preventDefault();
            }
        });
    });

    // -------------------------------------------------------------
    // 4. Permission Reason Modal Trigger
    // -------------------------------------------------------------
    const reasonModalEl = document.getElementById('permissionPreviewModal');
    if (reasonModalEl) {
        reasonModalEl.addEventListener('show.bs.modal', function (event) {
            const trigger = event.relatedTarget;
            if (!trigger) return;

            const employee = trigger.getAttribute('data-employee') || 'Unknown';
            const type = trigger.getAttribute('data-type') || 'Permission';
            const date = trigger.getAttribute('data-date') || '-';
            const duration = trigger.getAttribute('data-duration') || '-';
            const reason = trigger.getAttribute('data-reason') || 'No reason specified.';
            const status = trigger.getAttribute('data-status') || 'PENDING';

            const modalTitle = reasonModalEl.querySelector('#previewModalTitle');
            const modalEmployee = reasonModalEl.querySelector('#previewModalEmployee');
            const modalType = reasonModalEl.querySelector('#previewModalType');
            const modalDate = reasonModalEl.querySelector('#previewModalDate');
            const modalDuration = reasonModalEl.querySelector('#previewModalDuration');
            const modalStatus = reasonModalEl.querySelector('#previewModalStatus');
            const modalReason = reasonModalEl.querySelector('#previewModalReason');

            if (modalTitle) modalTitle.textContent = `Permission Request Letter — ${employee}`;
            if (modalEmployee) modalEmployee.textContent = employee;
            if (modalType) modalType.textContent = type;
            if (modalDate) modalDate.textContent = date;
            if (modalDuration) modalDuration.textContent = duration;
            if (modalStatus) {
                modalStatus.textContent = status;
                modalStatus.className = 'badge ' + (
                    status === 'APPROVED' ? 'bg-success' :
                    status === 'PENDING' ? 'bg-warning text-dark' : 'bg-danger'
                );
            }
            if (modalReason) modalReason.textContent = reason;
        });
    }

    // -------------------------------------------------------------
    // 5. Live Quick Search (Attendance, Sessions, Visits)
    // -------------------------------------------------------------
    function setupQuickSearch(inputId, containerSelector, itemSelector, emptyMsg) {
        const input = document.getElementById(inputId);
        const container = document.querySelector(containerSelector);
        if (!input || !container) return;

        input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
            }
        });

        input.addEventListener('input', function () {
            const query = this.value.trim().toLowerCase();
            const items = container.querySelectorAll(itemSelector);
            let visibleCount = 0;

            items.forEach(item => {
                const text = item.textContent.toLowerCase();
                const matches = text.includes(query);
                item.style.display = matches ? '' : 'none';
                if (matches) visibleCount++;
            });

            let noMatchEl = container.querySelector('.quick-search-empty-state');
            if (visibleCount === 0 && items.length > 0) {
                if (!noMatchEl) {
                    noMatchEl = document.createElement('div');
                    noMatchEl.className = 'col-12 quick-search-empty-state text-center text-muted py-5';
                    noMatchEl.innerHTML = `<i class="bi bi-search fs-3 d-block mb-2 text-muted"></i>${emptyMsg} "<strong>${escapeHtml(input.value)}</strong>"`;
                    container.appendChild(noMatchEl);
                } else {
                    noMatchEl.innerHTML = `<i class="bi bi-search fs-3 d-block mb-2 text-muted"></i>${emptyMsg} "<strong>${escapeHtml(input.value)}</strong>"`;
                    noMatchEl.style.display = '';
                }
            } else if (noMatchEl) {
                noMatchEl.style.display = 'none';
            }
        });
    }

    // Attendance Table Search
    const attendanceSearch = document.getElementById('attendanceQuickSearch');
    const attendanceTable = document.getElementById('attendanceTable');
    if (attendanceSearch && attendanceTable) {
        attendanceSearch.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
            }
        });
        attendanceSearch.addEventListener('input', function () {
            const query = this.value.trim().toLowerCase();
            const rows = attendanceTable.querySelectorAll('tbody tr:not(.empty-state-row)');
            let visibleCount = 0;

            rows.forEach(row => {
                const text = row.textContent.toLowerCase();
                const matches = text.includes(query);
                row.style.display = matches ? '' : 'none';
                if (matches) visibleCount++;
            });

            let noMatchRow = attendanceTable.querySelector('.search-empty-row');
            if (visibleCount === 0 && rows.length > 0) {
                if (!noMatchRow) {
                    noMatchRow = document.createElement('tr');
                    noMatchRow.className = 'search-empty-row text-center text-muted';
                    noMatchRow.innerHTML = `<td colspan="9" class="py-4">
                        <i class="bi bi-search me-1"></i> No matching attendance records found for "<strong>${escapeHtml(attendanceSearch.value)}</strong>"
                    </td>`;
                    attendanceTable.querySelector('tbody').appendChild(noMatchRow);
                } else {
                    noMatchRow.innerHTML = `<td colspan="9" class="py-4">
                        <i class="bi bi-search me-1"></i> No matching attendance records found for "<strong>${escapeHtml(attendanceSearch.value)}</strong>"
                    </td>`;
                    noMatchRow.style.display = '';
                }
            } else if (noMatchRow) {
                noMatchRow.style.display = 'none';
            }
        });
    }

    // Sessions Grid Quick Search
    setupQuickSearch('sessionsQuickSearch', '.row.g-4', '.col-md-6.col-lg-4', 'No work sessions found matching');

    // Visits Grid Quick Search
    setupQuickSearch('visitsQuickSearch', '.row.g-3.mb-4', '.col-md-6.col-xl-4', 'No field visits found matching');

    // -------------------------------------------------------------
    // 6. Generic Table Search Helper
    // -------------------------------------------------------------
    window.searchTable = function (inputId, tableId) {
        const input = document.getElementById(inputId);
        const table = document.getElementById(tableId);
        if (!input || !table) return;

        input.addEventListener('input', function () {
            const filter = input.value.toUpperCase();
            const tr = table.getElementsByTagName('tr');

            for (let i = 1; i < tr.length; i++) {
                let found = false;
                const td = tr[i].getElementsByTagName('td');
                for (let j = 0; j < td.length; j++) {
                    if (td[j]) {
                        const txtValue = td[j].textContent || td[j].innerText;
                        if (txtValue.toUpperCase().indexOf(filter) > -1) {
                            found = true;
                            break;
                        }
                    }
                }
                tr[i].style.display = found ? '' : 'none';
            }
        });
    };

    // -------------------------------------------------------------
    // 7. CSV Export Utility
    // -------------------------------------------------------------
    window.exportTableToCSV = function (tableId, filename) {
        const table = document.getElementById(tableId);
        if (!table) return;
        const rows = table.querySelectorAll('tr');
        const csv = [];

        rows.forEach(row => {
            if (row.style.display === 'none') return;
            const cols = row.querySelectorAll('td, th');
            const csvRow = [];
            cols.forEach(col => {
                let text = (col.innerText || '').trim();
                if (text.includes(',') || text.includes('"') || text.includes('\n')) {
                    text = '"' + text.replace(/"/g, '""') + '"';
                }
                csvRow.push(text);
            });
            if (csvRow.length > 0) {
                csv.push(csvRow.join(','));
            }
        });

        const blob = new Blob([csv.join('\n')], { type: 'text/csv;charset=utf-8;' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename || 'export.csv';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
    };

    function escapeHtml(string) {
        const div = document.createElement('div');
        div.innerText = string;
        return div.innerHTML;
    }
});
