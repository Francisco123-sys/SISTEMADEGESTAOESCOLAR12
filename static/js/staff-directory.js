(() => {
    document.querySelectorAll('[data-staff-directory]').forEach((directory) => {
        const searchInput = directory.querySelector('[data-staff-search]');
        const clearButton = directory.querySelector('[data-staff-clear]');
        const cards = [...directory.querySelectorAll('.staff-card')];
        const visibleCount = directory.querySelector('[data-staff-count]');
        const emptyMessage = directory.querySelector('[data-staff-empty]');

        if (!searchInput || !clearButton || !visibleCount || !emptyMessage) return;

        const normalize = (value) => value
            .toLocaleLowerCase('pt')
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .replace(/[^a-z0-9]/g, '')
            .trim();

        const filterCards = () => {
            const query = normalize(searchInput.value);
            let count = 0;
            cards.forEach((card) => {
                const matches = normalize(card.dataset.search || '').includes(query);
                card.hidden = !matches;
                if (matches) count += 1;
            });
            visibleCount.textContent = count;
            emptyMessage.hidden = count > 0 || cards.length === 0;
        };

        searchInput.addEventListener('input', filterCards);
        clearButton.addEventListener('click', () => {
            searchInput.value = '';
            filterCards();
            searchInput.focus();
        });
    });
})();
