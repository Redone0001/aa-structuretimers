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
        dropdownCssClass: "my_select2_dropdown"
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
        dropdownCssClass: "my_select2_dropdown"
    });

    $('.select2-render').addClass('form-select');

    $.datetimepicker.setLocale(languageCode);
    $('#timer-date-field').datetimepicker({ format: 'Y-m-d H:i', theme: 'default' });

    // Clear date field when time-remaining fields are used and vice versa
    $('.timer-time-remaining-field').change(function () {
        $('#timer-date-field').val('');
    });

    $('#timer-date-field').change(function () {
        $('.timer-time-remaining-field').val('');
    });
});
