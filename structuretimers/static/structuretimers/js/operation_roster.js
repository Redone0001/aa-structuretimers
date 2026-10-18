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
            const section = node('section', undefined, 'border rounded p-2 mb-2');
            section.append(node('h4', role.name, 'h6 mb-1'));
            section.append(node('p', role.systems.length ? 'Systems: ' + role.systems.join(', ') : 'No systems linked', 'small text-muted mb-1'));
            const list = node('ul', undefined, 'mb-0');
            for (const id of role.users) {assigned.add(id); list.append(node('li', value.people[String(id)] || 'Unavailable user'));}
            if (!role.users.length) list.append(node('li', 'Unfilled', 'text-muted'));
            section.append(list); root.append(section);
        }
        const others = (value.participants || []).filter(id => !assigned.has(id));
        if (others.length) {
            const section = node('section', undefined, 'border rounded p-2 mb-2'); section.append(node('h4', 'No role', 'h6'));
            const list = node('ul', undefined, 'mb-0');for (const id of others) list.append(node('li', value.people[String(id)] || 'Unavailable user'));
            section.append(list);root.append(section);
        }
        if (!(value.roles || []).length && !others.length) root.append(node('p', 'No roles or participants yet.', 'text-muted'));
    }
    window.OperationRoster = {render};
    if (root.dataset.editor !== 'true') {render(initial);return;}
    const templates = JSON.parse(document.getElementById('operation-role-templates').textContent);
    let roles = [];
    const selector = document.getElementById('operation-role-template');
    for (const template of templates) selector.append(new Option(template.name, template.id));
    function userPicker(select, ids) {
        for (const id of ids) select.append(new Option(people[String(id)] || 'Unavailable user', id, true, true));
        $(select).select2({theme: 'bootstrap', width: '100%', placeholder: 'Choose AA users', allowClear: true, minimumInputLength: 1,
            ajax: {url: root.dataset.usersUrl, dataType: 'json', delay: 250, data: params => ({term: params.term})}});
        $(select).on('select2:select', event => {people[String(event.params.data.id)] = event.params.data.text;});
    }
    const participants = document.getElementById('operation-participants');userPicker(participants, initial.participants || []);
    root.replaceChildren();
    function addRole(value) {
        const section = node('section', undefined, 'border rounded p-3 mb-3');section.dataset.roleEditor = 'true';
        const name = node('input');name.type = 'text';name.placeholder = 'Role name';name.value = value.name || '';name.maxLength = 200;name.required = true;name.className = 'form-control mb-2';name.setAttribute('aria-label', 'Role name');
        const systemLabel = node('label', 'Systems (optional)', 'form-label');
        const systems = node('select');systems.multiple = true;systems.setAttribute('aria-label', 'Role systems');
        for (const system of value.systems || []) systems.append(new Option(system, system, true, true));
        const userLabel = node('label', 'Users with this role (optional)', 'form-label mt-2');
        const users = node('select');users.multiple = true;users.setAttribute('aria-label', 'Users with this role');
        const remove = node('button', 'Remove role', 'btn btn-sm btn-outline-danger mt-2');remove.type = 'button';
        const entry = {name, systems, users, template_id: value.template_id || null};roles.push(entry);
        remove.onclick = () => {$(systems).select2('destroy');$(users).select2('destroy');roles = roles.filter(role => role !== entry);section.remove();};
        section.append(name, systemLabel, systems, userLabel, users, remove);root.append(section);
        $(systems).select2({theme: 'bootstrap', width: '100%', tags: true, placeholder: 'Choose or type systems', tokenSeparators: [','],
            ajax: {url: root.dataset.systemsUrl, dataType: 'json', delay: 250, data: params => ({term: params.term})}});
        userPicker(users, value.users || []);
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
            roles: roles.map(role => ({name: role.name.value, systems: $(role.systems).val() || [], users: ($(role.users).val() || []).map(Number), template_id: role.template_id})),
        });
    });
})();
