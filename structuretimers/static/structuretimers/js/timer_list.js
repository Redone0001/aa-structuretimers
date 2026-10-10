/* return duration as countdown string */
function durationToCountdownStr(duration) {
    let out = "";
    if (duration.years()) {
        out += duration.years() + "y ";
    }
    if (duration.months()) {
        out += duration.months() + "m ";
    }
    if (duration.days()) {
        out += duration.days() + "d ";
    }
    return (
        out +
        duration.hours() +
        "h " +
        duration.minutes() +
        "m " +
        duration.seconds() +
        "s"
    );
}

function getCurrentEveTimeString() {
    return moment().utc().format("dddd LL HH:mm:ss");
}

/* eve clock and timer countdown feature */
function updateClock() {
    document.getElementById("current-time").innerHTML = moment()
        .utc()
        .format("HH:mm");
}

/* return countdown to given date as string */
function dateToCountdownStr(date) {
    let duration = moment.duration(
        moment(date).utc() - moment(),
        "milliseconds"
    );
    if (duration > 0) {
        return durationToCountdownStr(duration);
    } else {
        return "ELAPSED";
    }
}

/* return local time and countdown string to given date as HTML*/
function localTimeOutputHtml(date) {
    return moment(date).format("ddd @ LT") + "<br>" + dateToCountdownStr(date);
}

function createVisibleColumDef(idxStart) {
    return {
        visible: false,
        targets: [
            idxStart,
            idxStart + 1,
            idxStart + 2,
            idxStart + 3,
            idxStart + 4,
            idxStart + 5,
            idxStart + 6,
            idxStart + 7,
        ],
    };
}

function createFilterDefinition(
    idxStart,
    hasPermOPSEC,
    titleSolarSystem,
    titleRegion,
    titleStructureType,
    titleTimerType,
    titleObjective,
    titleVisibility,
    titleOwner
) {
    const definition = {
        columns: [
            {
                idx: idxStart,
                title: titleSolarSystem,
            },
            {
                idx: idxStart + 1,
                title: titleRegion,
            },
            {
                idx: idxStart + 2,
                title: titleStructureType,
            },
            {
                idx: idxStart + 3,
                title: titleTimerType,
            },
            {
                idx: idxStart + 4,
                title: titleObjective,
            },
            {
                idx: idxStart + 5,
                title: titleVisibility,
            },
            {
                idx: idxStart + 6,
                title: titleOwner,
                maxWidth: "12em",
            },
        ],
    };
    if (hasPermOPSEC) {
        definition.columns.push({
            idx: idxStart + 7,
            title: "OPSEC",
        });
    }
    return definition;
}

function buildMultiValueSearchRegex(values) {
    if (!values.length) {
        return "";
    }

    const escapedValues = values.map(function (value) {
        return $.fn.dataTable.util.escapeRegex(value);
    });
    return "^(?:" + escapedValues.join("|") + ")$";
}

function initializeMultiSelectFilters(table, filterDefinition, titleFilterBy, titleAll, savedFilters = {}) {
    const tableId = table.table().node().id;
    const exportData = document.getElementById("dataExport").dataset;
    const wrapper = $("<div>", {class: "timer-filter-wrapper mb-3"});
    const row = $("<div>", {class: "timer-filter-row"}).append(
        $("<p>", {class: "mb-1 fw-bold"}).text(titleFilterBy + ":"), wrapper
    );
    $(table.table().container()).prepend(row);
    const windowPrefix = {tbl_manage_recon: 'recon'}[tableId];
    if (windowPrefix) {
        row.append($('#' + windowPrefix + '-window-filter'));
    }
    filterDefinition.columns.forEach(function (definition) {
        const column = table.column(definition.idx);
        const id = tableId + "_filterSelect" + definition.idx;
        const group = $("<div>", {class: "timer-filter-group dropdown"});
        const title = $("<span>", {id: id + "_label", class: "form-label mb-1 d-block"}).text(definition.title);
        const summary = $("<span>", {class: "timer-filter-summary"}).text(titleAll);
        const toggle = $("<button>", {
            id: id, type: "button", class: "btn timer-filter-toggle dropdown-toggle",
            "data-bs-toggle": "dropdown", "data-bs-auto-close": "outside",
            "aria-expanded": "false", "aria-labelledby": id + "_label " + id + "_value",
            "aria-controls": id + "_menu",
        }).append(summary.attr("id", id + "_value"));
        const menu = $("<div>", {id: id + "_menu", class: "dropdown-menu timer-filter-menu p-2"});
        const search = $("<input>", {type: "search", class: "form-control form-control-sm mb-2",
            placeholder: exportData.filterSearchLabel, "aria-label": exportData.filterSearchLabel + ": " + definition.title});
        const clear = $("<button>", {type: "button", class: "btn btn-sm btn-outline-secondary timer-filter-clear w-100 mt-2"}).text(exportData.filterClearLabel);
        const options = $("<div>", {class: "timer-filter-options", role: "group", "aria-labelledby": id + "_label"});
        const empty = $("<p>", {class: "small text-muted mb-0", hidden: true}).text(exportData.filterEmptyLabel);
        const selected = new Set(Array.isArray(savedFilters[definition.idx]) ? savedFilters[definition.idx] : []);
        const checks = [];
        function update() {
            const values = Array.from(selected);
            summary.text(values.length === 0 ? titleAll : values.length === 1 ? values[0] : exportData.filterSelectedLabel.replace("%(count)s", values.length));
            toggle.attr("title", values.length ? values.join(", ") : titleAll);
            clear.prop("disabled", !values.length);
            column.search(buildMultiValueSearchRegex(values), true, false).draw();
        }
        column.data().unique().sort().each(function (value) {
            if (value === null || value === undefined || value === "") return;
            const text = String(value);
            const checkbox = $("<input>", {type: "checkbox", class: "form-check-input", value: text, checked: selected.has(text)});
            const label = $("<label>", {class: "timer-filter-option"}).append(checkbox, $("<span>").text(text));
            checkbox.on("change", function () {
                if (this.checked) selected.add(text); else selected.delete(text);
                update();
            });
            checks.push({checkbox, label, text});
            options.append(label);
        });
        search.on("input", function () {
            const query = this.value.toLocaleLowerCase().trim();
            let visible = 0;
            checks.forEach(function (item) {
                const match = item.text.toLocaleLowerCase().includes(query);
                item.label.prop("hidden", !match);
                if (match) visible += 1;
            });
            empty.prop("hidden", visible !== 0);
        });
        clear.prop("disabled", true).on("click", function () {
            selected.clear(); checks.forEach(item => item.checkbox.prop("checked", false)); update();
        });
        group.on("shown.bs.dropdown", function () { search.trigger("focus"); });
        group.on("hidden.bs.dropdown", function () { search.val("").trigger("input"); });
        menu.append(search, options, empty, clear);
        wrapper.append(group.append(title, toggle, menu));
        if (selected.size) update();
    });
}

$(document).ready(function () {
    /* retrieve generated data from HTML page */
    const elem = document.getElementById("dataExport");
    function initializeAssignees() {
        $("#tbl_timers_current .timer-assignee").not(".select2-hidden-accessible").each(function () {
            const select = $(this);
            select.select2({
                theme: "bootstrap",
                dropdownCssClass: "st-select2-dropdown",
                width: "100%",
                placeholder: select.attr("data-placeholder"),
                allowClear: true,
                minimumInputLength: 1,
                ajax: {
                    url: select.attr("data-url"),
                    dataType: "json",
                    delay: 250,
                    data: params => ({term: params.term, page: params.page || 1}),
                },
            });
            select.next(".select2").find(".select2-selection").attr("aria-labelledby",
                select.attr("id") + "-label select2-" + select.attr("id") + "-container");
        });
    }
    $("#tbl_timers_current").on("draw.dt", initializeAssignees);
    $("#tbl_timers_current").on("select2:open", ".timer-assignee", function () {
        const search = document.querySelector(".select2-container--open .select2-search__field");
        if (search) {
            search.setAttribute("aria-label", elem.dataset.assignmentSearch);
            search.focus();
        }
    });
    $("#tbl_timers_current").on("change", ".timer-assignee", function () {
        const select = $(this);
        const status = select.siblings(".assignment-status");
        const previous = select.attr("data-saved-value");
        select.prop("disabled", true);
        status.removeClass("text-danger").text(elem.dataset.assignmentSaving);
        $.ajax({
            url: select.attr("data-url"),
            method: "POST",
            data: {
                assigned_to: select.val() || "",
                csrfmiddlewaretoken: $("#timer-assignment-csrf input").val(),
            },
        }).done(function () {
            select.attr("data-saved-value", select.val() || "");
            status.text(elem.dataset.assignmentSaved);
            select.prop("disabled", false);
            select.find("option").each(function () {
                $(this).attr("selected", this.selected ? "selected" : null);
            });
            select.select2("destroy");
            const cell = select.closest("td");
            $("#tbl_timers_current").DataTable().cell(cell).data(cell.html());
            initializeAssignees();
            cell.find(".select2-selection").trigger("focus");
        }).fail(function (xhr) {
            select.val(previous).trigger("change.select2");
            status.addClass("text-danger").text(
                (xhr.responseJSON && xhr.responseJSON.error) || elem.dataset.assignmentError
            );
        }).always(function () {
            select.prop("disabled", false);
        });
    });
    const listDataCurrentUrl = elem.getAttribute("data-listDataCurrentUrl");
    const listDataPastUrl = elem.getAttribute("data-listDataPastUrl");
    const listDataTargetUrl = elem.getAttribute("data-listDataTargetUrl");
    const getTimerDataUrl = elem.getAttribute("data-getTimerDataUrl");
    const titleSolarSystem = elem.getAttribute("data-titleSolarSystem");
    const titleRegion = elem.getAttribute("data-titleRegion");
    const titleStructureType = elem.getAttribute("data-titleStructureType");
    const titleTimerType = elem.getAttribute("data-titleTimerType");
    const titleObjective = elem.getAttribute("data-titleObjective");
    const titleOwner = elem.getAttribute("data-titleOwner");
    const titleVisibility = elem.getAttribute("data-titleVisibility");
    const titleFilterBy = elem.getAttribute("data-titleFilterBy");
    const titleAll = elem.getAttribute("data-titleAll");
    const hasPermOPSEC = elem.getAttribute("data-hasPermOPSEC") == "True";
    const dataTablesPageLength = Number(
        elem.getAttribute("data-dataTablesPageLength")
    );
    const dataTablesPaging =
        elem.getAttribute("data-dataTablesPaging") == "True";
    const tabId = elem.getAttribute("data-tabId");

    /* activate selected tab */
    $('button[data-bs-target="#' + tabId + '"]').tab("show");

    /* Update modal with requested timer */
    $("#modalTimerDetails").on("show.bs.modal", function (event) {
        const timer_pk = $(event.relatedTarget).data("timerpk");
        const detailUrl = $(event.relatedTarget).data("detailUrl");

        $("#modalLoadError").html("");
        $("#modalContent").hide();
        $("#modal_div_spinner").show();
        $("#modalContent").load(
            detailUrl || getTimerDataUrl.replace("pk_dummy", timer_pk),
            function (responseText, textStatus, req) {
                $("#modal_div_spinner").hide();
                $("#modalContent").show();
                if (textStatus == "error") {
                    console.log(req);
                    $("#modalLoadError").html(
                        '<p class="text-danger">An unexpected error occured: ' +
                            req.status +
                            " " +
                            req.statusText +
                            '</p><p class="text-danger">' +
                            "Please close this window and try again.</p>"
                    );
                }
            }
        );
    });

    /* build dataTables */
    let columns = [
        {
            data: "date",
            render: function (data, type, row) {
                return moment(data).utc().format("YYYY-MM-DD HH:mm");
            },
        },
        {
            data: "local_time",
            render: function (data, type, row) {
                return localTimeOutputHtml(data);
            },
        },
        { data: "location" },
        {
            data: "distance",
            render: {
                _: "display",
                sort: "sort",
            },
        },
        { data: "structure_details" },
        { data: "owner" },
        { data: "name_objective" },
        { data: "assignment", orderable: false, className: "st-assignment-cell" },
        { data: "actions" },

        /* hidden columns */
        { data: "system_name" },
        { data: "region_name" },
        { data: "structure_type_name" },
        { data: "timer_type_name" },
        { data: "objective_name" },
        { data: "visibility" },
        { data: "owner_name" },
        { data: "opsec_str" },
    ];
    let lengthMenu = [
        [10, 25, 50, 100, -1],
        [10, 25, 50, 100, "All"],
    ];
    let idxStart = 9;
    let columnDefs = [
        { sortable: false, targets: [idxStart - 1] },
        createVisibleColumDef(idxStart),
    ];
    const standardFilterDefinition = createFilterDefinition(
        idxStart,
        hasPermOPSEC,
        titleSolarSystem,
        titleRegion,
        titleStructureType,
        titleTimerType,
        titleObjective,
        titleVisibility,
        titleOwner
    );

    $("#tbl_timers_past").DataTable({
        ajax: {
            url: listDataPastUrl,
            dataSrc: "",
            cache: false,
        },
        columns: columns,
        order: [[0, "desc"]],
        lengthMenu: lengthMenu,
        paging: dataTablesPaging,
        pageLength: dataTablesPageLength,
        columnDefs: columnDefs,
        initComplete: function () {
            initializeMultiSelectFilters(
                this.api(),
                standardFilterDefinition,
                titleFilterBy,
                titleAll
            );
        },
    });
    const table_current = $("#tbl_timers_current").DataTable({
        ajax: {
            url: listDataCurrentUrl,
            dataSrc: "",
            cache: false,
        },
        columns: columns,
        order: [[0, "asc"]],
        lengthMenu: lengthMenu,
        paging: dataTablesPaging,
        pageLength: dataTablesPageLength,
        columnDefs: columnDefs,
        initComplete: function () {
            initializeMultiSelectFilters(
                this.api(),
                standardFilterDefinition,
                titleFilterBy,
                titleAll
            );
        },
        createdRow: function (row, data, dataIndex) {
            if (data["is_passed"]) {
                $(row).addClass("table-active");
            } else if (data["is_important"]) {
                $(row).addClass("table-warning");
            }
        },
    });

    function updateTimers() {
        table_current.rows().every(function () {
            var d = this.data();
            if (!d["is_passed"]) {
                // Refresh only the countdown cell. Replacing the whole row
                // recreates the assignment select and closes its open menu.
                table_current.cell(this.index(), 1).data(d["date"]);
            }
        });
    }

    function timedUpdate() {
        updateClock();
        updateTimers();
    }

    // Start timed updates
    setInterval(timedUpdate, 1000);
});

// View fit / Copy fit buttons on timers and Database records.
$(document).on("click", ".st-view-fit", function () {
    $("#modalFitText").val(this.dataset.fit);
    bootstrap.Modal.getOrCreateInstance(document.getElementById("modalFit")).show();
});
$(document).on("click", ".st-copy-fit, #modalFitCopy", function () {
    const button = this;
    const text = button.id === "modalFitCopy" ? $("#modalFitText").val() : button.dataset.fit;
    const done = () => {
        const original = button.getAttribute("title");
        button.setAttribute("title", "Copied");
        button.classList.add("btn-success");
        setTimeout(() => {
            button.classList.remove("btn-success");
            if (original) button.setAttribute("title", original);
        }, 1500);
    };
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done);
    } else {
        // Clipboard API needs HTTPS; fall back to a temporary text area.
        const area = document.createElement("textarea");
        area.value = text;
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
        done();
    }
});
