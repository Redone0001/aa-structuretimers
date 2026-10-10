/* Add information: paste, Parse or Manual, then the full form. */
$(function () {
    'use strict';
    const root = document.getElementById('st-add-info');
    const form = document.getElementById('add-timer-form');
    if (!root || !form) return;
    const paste = document.getElementById('id_paste_info');
    const result = document.getElementById('st-add-info-result');
    const groups = () => [...form.querySelectorAll('.mb-3')].filter(g => !g.contains(paste));
    const submitRow = [...form.children].filter(el => el.tagName === 'BUTTON' || el.tagName === 'P' || el.tagName === 'BR' || el.matches('a'));

    function showForm() {
        groups().forEach(g => { g.hidden = false; });
        submitRow.forEach(el => { el.hidden = false; });
        // After the first step, Parse sits next to the paste box at the top.
        paste.closest('.mb-3').after(document.getElementById('st-add-info-actions'));
        document.getElementById('st-add-info-manual').hidden = true;
        document.getElementById('st-add-info-intro').hidden = true;
        $('.select2-solar-systems, .select2-structure-types, .select2-owners, .select2-database-entries').trigger('change.select2');
    }
    function showPasteOnly() {
        groups().forEach(g => { g.hidden = true; });
        submitRow.forEach(el => { el.hidden = true; });
        paste.closest('.mb-3').after(document.getElementById('st-add-info-actions'));
        paste.focus();
    }
    function setSelect(selector, option) {
        if (!option) return;
        $(selector).empty().append(new Option(option.text, option.id, true, true)).trigger('change');
    }
    function setValue(id, value) {
        const field = document.getElementById(id);
        if (field && value !== undefined && value !== null && value !== '') field.value = value;
    }
    function note(text, kind) {
        result.replaceChildren();
        const p = document.createElement('div');
        p.className = 'alert alert-' + kind + ' py-2 mb-0';
        p.textContent = text;
        result.append(p);
    }

    async function parse() {
        const button = document.getElementById('st-add-info-parse');
        button.disabled = true;
        try {
            const body = new FormData();
            body.append('text', paste.value);
            const response = await fetch(root.dataset.parseUrl, {
                method: 'POST', credentials: 'same-origin', body,
                headers: {'X-CSRFToken': form.querySelector('[name=csrfmiddlewaretoken]').value},
            });
            if (!response.ok) throw new Error('Could not read the paste (' + response.status + ').');
            const data = await response.json();
            const v = data.values;
            showForm();
            if (data.match) {
                // Known structure: fill everything from the record, then the paste on top.
                setSelect('.select2-database-entries', data.match);
                $('.select2-database-entries').trigger({type: 'select2:select', params: {data: data.match}});
            }
            setSelect('.select2-solar-systems', v.solar_system);
            setSelect('.select2-structure-types', v.structure_type);
            setSelect('.select2-owners', v.owner);
            setValue('id_structure_name', v.structure_name);
            setValue('timer-date-field', v.date);
            setValue('id_location_details', v.location_details);
            setValue('id_fitting', v.fitting);
            if (!data.found.length) {
                note('Nothing could be read from this paste. Fill in the boxes below, or paste a timer or an EFT fitting.', 'warning');
            } else if (data.match) {
                note('This is ' + data.match.text + ', already in the Database. Saving updates it instead of adding a new entry.', 'success');
            } else {
                note('Filled in what could be read. No matching structure is in the Database yet, so saving adds it.', 'info');
            }
        } catch (error) {
            showForm();
            note(error.message, 'danger');
        } finally {
            button.disabled = false;
        }
    }

    document.getElementById('st-add-info-parse').addEventListener('click', parse);
    document.getElementById('st-add-info-manual').addEventListener('click', () => { showForm(); result.replaceChildren(); });
    if (root.dataset.start === 'form') showForm(); else showPasteOnly();
});
