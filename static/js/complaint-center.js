(() => {
    const mobileViewport = window.matchMedia('(max-width: 760px)');

    window.initComplaintWorkspace = (workspace) => {
        if (!workspace) return;
        const list = workspace.querySelector('.complaint-master-list');
        const details = [...workspace.querySelectorAll('[data-complaint-detail]')];
        const items = [...workspace.querySelectorAll('[data-complaint-select]')];
        if (!list || !details.length || !items.length) return;

        const showComplaint = (id, moveFocus = false) => {
            const detail = details.find((item) => item.dataset.complaintDetail === String(id));
            const selectedItem = items.find((item) => item.dataset.complaintSelect === String(id));
            if (!detail || !selectedItem) return;

            details.forEach((item) => { item.hidden = item !== detail; });
            const detailColumn = workspace.querySelector('.complaint-details-column');
            if (detailColumn) {
                const profiles = [...detailColumn.querySelectorAll('[data-complaint-details]')];
                const profilePlaceholder = detailColumn.querySelector('.complaint-details-placeholder');
                profiles.forEach((profile) => {
                    profile.hidden = profile.dataset.complaintDetails !== String(id);
                });
                if (profilePlaceholder) profilePlaceholder.hidden = profiles.length > 0;
            }
            items.forEach((item) => {
                const active = item === selectedItem;
                item.classList.toggle('is-selected', active);
                item.setAttribute('aria-expanded', String(active));
            });
            workspace.classList.add('has-selection');
            workspace.classList.toggle('complaint-mobile-detail', mobileViewport.matches);
            if (moveFocus) {
                const heading = detail.querySelector('h2');
                heading?.setAttribute('tabindex', '-1');
                heading?.focus({preventScroll: true});
                detail.scrollIntoView({block: 'start', behavior: 'smooth'});
            }
        };

        const closeComplaint = (returnFocus = true) => {
            const selectedItem = items.find(
                (item) => item.getAttribute('aria-expanded') === 'true'
            );
            details.forEach((item) => { item.hidden = true; });
            items.forEach((item) => {
                item.classList.remove('is-selected');
                item.setAttribute('aria-expanded', 'false');
            });
            const detailColumn = workspace.querySelector('.complaint-details-column');
            if (detailColumn) {
                detailColumn.querySelectorAll('[data-complaint-details]').forEach((profile) => {
                    profile.hidden = true;
                });
                const placeholder = detailColumn.querySelector('.complaint-details-placeholder');
                if (placeholder) placeholder.hidden = false;
            }
            workspace.classList.remove('has-selection', 'complaint-mobile-detail');
            if (returnFocus) selectedItem?.focus();
            if (window.location.hash.startsWith('#complaint-')) {
                window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
            }
        };

        items.forEach((item) => {
            item.addEventListener('click', () => {
                showComplaint(item.dataset.complaintSelect, true);
            });
        });
        workspace.querySelectorAll('[data-complaint-back]').forEach((button) => {
            button.addEventListener('click', () => closeComplaint());
        });
        const hashMatch = window.location.hash.match(/^#complaint-(\d+)$/);
        if (hashMatch && items.some((item) => item.dataset.complaintSelect === hashMatch[1])) {
            showComplaint(hashMatch[1]);
        } else if (!mobileViewport.matches && items.length) {
            showComplaint(items[0].dataset.complaintSelect);
        }
    };

    if (!window.__complaintCenterHashHandlerBound) {
        window.__complaintCenterHashHandlerBound = true;
        window.addEventListener('hashchange', () => {
            const match = window.location.hash.match(/^#complaint-(\d+)$/);
            if (!match) return;
            document.querySelectorAll('[data-complaint-workspace]').forEach((workspace) => {
                window.initComplaintWorkspace(workspace);
                const item = workspace.querySelector(
                    `[data-complaint-select="${match[1]}"]`
                );
                item?.click();
            });
        });
    }

    if (!window.__complaintCenterViewportHandlerBound) {
        window.__complaintCenterViewportHandlerBound = true;
        mobileViewport.addEventListener('change', () => {
            document.querySelectorAll('[data-complaint-workspace].has-selection')
                .forEach((workspace) => {
                    workspace.classList.toggle(
                        'complaint-mobile-detail',
                        mobileViewport.matches
                    );
                });
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('[data-complaint-workspace]').forEach(
            window.initComplaintWorkspace
        );
    });
})();
