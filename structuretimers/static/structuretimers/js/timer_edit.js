$(document).ready(function () {
    const elem = document.getElementById('dataExport');
    const select2SolarSystemsUrl = elem.getAttribute('data-select2SolarSystemsUrl');
    const select2StructureTypesUrl = elem.getAttribute('data-select2StructureTypesUrl');
    const myTheme = "bootstrap";
    // Widget colors come from the active theme's CSS variables.
    let languageCode = JSON.parse(document.getElementById('language-code-data').textContent);

    // mapping of language codes from Django to datetimepicker widget
    if (languageCode === "zh-hans") {
        languageCode = "zh";
    }

    $('.select2-solar-systems').select2({
        ajax: {
            url: select2SolarSystemsUrl,
            dataType: 'json'
        },
        theme: myTheme,
        width: "100%",
        minimumInputLength: 2,
        placeholder: "Enter name of solar system",
        dropdownCssClass: "st-select2-dropdown"
    });

    $('.select2-structure-types').select2({
        ajax: {
            url: select2StructureTypesUrl,
            dataType: 'json'
        },
        theme: myTheme,
        width: "100%",
        minimumInputLength: 2,
        placeholder: "Enter name of structure type",
        dropdownCssClass: "st-select2-dropdown"
    });

    $('.select2-render').addClass('form-select');
    $('#id_assigned_to').select2({theme: myTheme, width: '100%',
        dropdownCssClass: 'st-select2-dropdown'});
    $('#add-timer-form select').each(function () {
        const label = document.querySelector('label[for="' + this.id + '"]');
        if (!label) return;
        label.id = this.id + '-label';
        $(this).next('.select2').find('.select2-selection').attr('aria-labelledby', label.id);
    });
    $('#add-timer-form').on('select2:open', 'select', function () {
        const search = document.querySelector('.select2-container--open .select2-search__field');
        const label = document.querySelector('label[for="' + this.id + '"]');
        if (search && label) { search.setAttribute('aria-label', label.textContent.trim()); search.focus(); }
    });
    $('#add-timer-form').on('submit', function (event) {
        if (this.dataset.submitting) { event.preventDefault(); return; }
        this.dataset.submitting = 'true';
        $(this).find('[type="submit"]').prop('disabled', true);
    });
    $(window).on('pageshow', function () {
        $('#add-timer-form').removeAttr('data-submitting').find('[type="submit"]').prop('disabled', false);
    });

    $.datetimepicker.setLocale(languageCode);
    $('#timer-date-field').datetimepicker({ format: 'Y-m-d H:i', theme: 'default', onGenerate: function () { this.addClass('st-datepicker'); } });

    // Clear date field when time-remaining fields are used and vice versa
    $('.timer-time-remaining-field').change(function () {
        $('#timer-date-field').val('');
    });

    $('#timer-date-field').change(function () {
        $('.timer-time-remaining-field').val('');
    });
});
