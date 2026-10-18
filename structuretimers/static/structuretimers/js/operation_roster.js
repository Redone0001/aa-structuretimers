/* Role templates are copied into the editor; all roster writes remain operation-local. */
(function () {
    'use strict';
    const source = document.getElementById('operation-roster-data');
    if (!source) return;
    const initial = JSON.parse(source.textContent), people = initial.people || {};
    const root = document.getElementById('operation-roster');
    const node = (tag, text, classes) => {const element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (classes) element.className = classes; return element;};
    function render(value) {
        root.replaceChildren();
        const assigned = new Set();
        for (const role of value.roles || []) {
            const column = node('div', undefined, 'col-12 col-md-6 col-xl-4');
            const section = node('section', undefined, 'card h-100');
            const body = node('div', undefined, 'card-body');
            section.append(node('h4', role.name, 'card-header h6 mb-0'));
            body.append(node('p', role.systems.length ? 'Systems: ' + role.systems.join(', ') : 'No systems linked', 'small text-muted mb-1'));
            const list = node('ul', undefined, 'mb-0');
            for (const id of role.users) {
                assigned.add(id);
                const person = node('li', value.people[String(id)] || 'Unavailable user');
                const coverage = role.user_systems?.[String(id)] || [];
                person.append(node('div', coverage.length ? 'Systems: ' + coverage.join(', ') : role.systems.length ? 'No systems assigned' : 'No system required', 'small text-muted'));
                list.append(person);
            }
            if (!role.users.length) list.append(node('li', 'Unfilled', 'text-muted'));
            body.append(list);section.append(body);column.append(section);root.append(column);
        }
        const others = (value.participants || []).filter(id => !assigned.has(id));
        if (others.length) {
            const column = node('div', undefined, 'col-12 col-md-6 col-xl-4');
            const section = node('section', undefined, 'card h-100');section.append(node('h4', 'No role', 'card-header h6 mb-0'));
            const list = node('ul', undefined, 'mb-0');for (const id of others) list.append(node('li', value.people[String(id)] || 'Unavailable user'));
            const body = node('div', undefined, 'card-body');body.append(list);section.append(body);column.append(section);root.append(column);
        }
        if (!(value.roles || []).length && !others.length) root.append(node('p', 'No roles or participants yet.', 'col-12 text-muted'));
    }
    window.OperationRoster = {render};
    if (root.dataset.editor !== 'true') {render(initial);return;}
    const templates = JSON.parse(document.getElementById('operation-role-templates').textContent);
    let roles = [], nextRole = 0;
    const selector = document.getElementById('operation-role-template');
    for (const template of templates) selector.append(new Option(template.name, template.id));
    function userPicker(select, ids) {
        for (const id of ids) select.append(new Option(people[String(id)] || 'Unavailable user', id, true, true));
        $(select).select2({theme: 'bootstrap', dropdownCssClass: 'st-select2-dropdown', width: '100%', placeholder: 'Choose AA users', allowClear: true, minimumInputLength: 1,
            ajax: {url: root.dataset.usersUrl, dataType: 'json', delay: 250, data: params => ({term: params.term})}});
        $(select).on('select2:select', event => {people[String(event.params.data.id)] = event.params.data.text;});
    }
    const participants = document.getElementById('operation-participants');userPicker(participants, initial.participants || []);
    root.replaceChildren();
    function addRole(value) {
        const section = node('section', undefined, 'card mb-3');section.dataset.roleEditor = 'true';
        const key = 'operation-role-' + (++nextRole);
        const header = node('div', undefined, 'card-header d-flex flex-wrap gap-2 align-items-center');
        const body = node('div', undefined, 'card-body row g-3');
        const name = node('input');name.id = key + '-name';name.type = 'text';name.placeholder = 'Role name';name.value = value.name || '';name.maxLength = 200;name.required = true;name.className = 'form-control st-role-name';name.setAttribute('aria-label', 'Role name');
        const systemLabel = node('label', 'Systems (optional)', 'form-label');
        const systems = node('select');systems.id = key + '-systems';systemLabel.htmlFor = systems.id;systems.multiple = true;systems.setAttribute('aria-label', 'Role systems');
        for (const system of value.systems || []) systems.append(new Option(system, system, true, true));
        const userLabel = node('label', 'Users with this role (optional)', 'form-label');
        const users = node('select');users.id = key + '-users';userLabel.htmlFor = users.id;users.multiple = true;users.setAttribute('aria-label', 'Users with this role');
        const remove = node('button', 'Remove role', 'btn btn-sm btn-outline-danger');remove.type = 'button';
        const coverage = node('div', undefined, 'col-12');
        const entry = {name, systems, users, coverage: {...(value.user_systems || {})}, systemPickers: new Map(), template_id: value.template_id || null};roles.push(entry);
        function refreshCoverage() {
            for (const [id, select] of entry.systemPickers) {entry.coverage[id] = $(select).val() || [];$(select).select2('destroy');}
            entry.systemPickers.clear();coverage.replaceChildren();
            const available = $(systems).val() || [], selected = ($(users).val() || []).map(String);
            entry.coverage = Object.fromEntries(selected.map(id => [id, (entry.coverage[id] || []).filter(system => available.includes(system))]));
            if (!available.length) {coverage.append(node('p', 'This role has no linked systems; its users do not need system assignments.', 'form-text mb-0'));return;}
            coverage.append(node('h5', 'Systems covered by each user', 'h6'));
            if (!selected.length) {coverage.append(node('p', 'Choose users above to assign one or more of the role systems to each person.', 'form-text mb-0'));return;}
            for (const id of selected) {
                const row = node('div', undefined, 'row g-2 align-items-center mb-2');
                const label = node('label', [...users.options].find(option => option.value === id)?.textContent || people[id] || 'Unavailable user', 'col-12 col-md-4 col-form-label');
                const column = node('div', undefined, 'col-12 col-md-8');
                const select = node('select');select.id = key + '-coverage-' + id;select.multiple = true;label.htmlFor = select.id;select.setAttribute('aria-label', 'Systems covered by ' + label.textContent);
                for (const system of available) select.append(new Option(system, system, entry.coverage[id].includes(system), entry.coverage[id].includes(system)));
                column.append(select);row.append(label, column);coverage.append(row);
                $(select).select2({theme: 'bootstrap', dropdownCssClass: 'st-select2-dropdown', width: '100%', placeholder: 'Choose one or more role systems', allowClear: true});
                entry.systemPickers.set(id, select);
            }
        }
        remove.onclick = () => {for (const select of entry.systemPickers.values()) $(select).select2('destroy');$(systems).select2('destroy');$(users).select2('destroy');roles = roles.filter(role => role !== entry);section.remove();};
        const systemColumn = node('div', undefined, 'col-12 col-lg-6');systemColumn.append(systemLabel, systems);
        const userColumn = node('div', undefined, 'col-12 col-lg-6');userColumn.append(userLabel, users);
        header.append(name, remove);body.append(systemColumn, userColumn, coverage);section.append(header, body);root.append(section);
        $(systems).select2({theme: 'bootstrap', dropdownCssClass: 'st-select2-dropdown', width: '100%', tags: true, placeholder: 'Choose or type systems', tokenSeparators: [','],
            ajax: {url: root.dataset.systemsUrl, dataType: 'json', delay: 250, data: params => ({term: params.term})}});
        userPicker(users, value.users || []);
        $(systems).on('change', refreshCoverage);$(users).on('change', refreshCoverage);refreshCoverage();
    }
    for (const role of initial.roles || []) addRole(role);
    function copyTemplate(template) {
        if (roles.some(role => role.name.value.trim().toLocaleLowerCase() === template.name.toLocaleLowerCase())) return;
        addRole({name: template.name, systems: [...template.systems], users: [], template_id: template.id});
    }
    document.getElementById('operation-add-template').onclick = () => {const template = templates.find(item => item.id === Number(selector.value));if (template) copyTemplate(template);};
    document.getElementById('operation-add-all-roles').onclick = () => templates.forEach(copyTemplate);
    document.getElementById('operation-add-role').onclick = () => addRole({name: '', systems: [], users: []});
    document.getElementById('operation-form').addEventListener('submit', () => {
        document.getElementById('id_roster').value = JSON.stringify({
            participants: ($(participants).val() || []).map(Number),
            roles: roles.map(role => ({name: role.name.value, systems: $(role.systems).val() || [], users: ($(role.users).val() || []).map(Number), user_systems: Object.fromEntries(($(role.users).val() || []).map(id => [String(id), role.systemPickers.has(String(id)) ? $(role.systemPickers.get(String(id))).val() || [] : role.coverage[String(id)] || []])), template_id: role.template_id})),
        });
    });
})();
