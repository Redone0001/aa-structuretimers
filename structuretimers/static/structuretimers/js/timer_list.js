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

function initializeMultiSelectFilters(
    table,
    filterDefinition,
    titleFilterBy,
    titleAll
) {
    const tableId = table.table().node().id;
    const filterWrapperId = tableId + "_filterWrapper";
    const filterWrapper = $("<div>", {
        id: filterWrapperId,
        class: "timer-filter-wrapper align-items-start d-flex flex-wrap gap-3 mb-3",
    });
    const filterRow = $("<div>", {
        class: "row justify-content-between timer-filter-row",
    });
    filterRow.append(
        $("<p>", { class: "mb-1 fw-bold" }).text(titleFilterBy + ":")
    );
    filterRow.append(filterWrapper);
    $(table.table().container()).prepend(filterRow);

    filterDefinition.columns.forEach(function (filterColumn) {
        const column = table.column(filterColumn.idx);
        const selectId = tableId + "_filterSelect" + filterColumn.idx;
        const filterGroup = $("<div>", { class: "timer-filter-group" });
        if (filterColumn.maxWidth) {
            filterGroup.css("max-width", filterColumn.maxWidth);
        }

        const label = $("<label>", {
            for: selectId,
            class: "form-label mb-1",
        }).text(filterColumn.title);
        const select = $("<select>", {
            id: selectId,
            class: "form-select timer-filter-select",
            multiple: "multiple",
            "aria-label": filterColumn.title,
        });

        column
            .data()
            .unique()
            .sort()
            .each(function (value) {
                if (value !== null && value !== undefined && value !== "") {
                    const textValue = String(value);
                    select.append(
                        $("<option>", {
                            value: textValue,
                            text: textValue,
                        })
                    );
                }
            });

        select.on("change", function () {
            const selectedValues = $(this).val() || [];
            column
                .search(
                    buildMultiValueSearchRegex(selectedValues),
                    true,
                    false
                )
                .draw();
        });

        filterGroup.append(label, select);
        filterWrapper.append(filterGroup);
        select.select2({
            allowClear: true,
            closeOnSelect: false,
            placeholder: titleAll,
            theme: "bootstrap",
            width: "100%",
        });
    });
}

$(document).ready(function () {
    /* retrieve generated data from HTML page */
    const elem = document.getElementById("dataExport");
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

        $("#modalLoadError").html("");
        $("#modalContent").hide();
        $("#modal_div_spinner").show();
        $("#modalContent").load(
            getTimerDataUrl.replace("pk_dummy", timer_pk),
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
    let idxStart = 8;
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
    const preliminaryFilterDefinition = createFilterDefinition(
        7,
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
    $("#tbl_preliminary").DataTable({
        ajax: {
            url: listDataTargetUrl,
            dataSrc: "",
            cache: false,
        },
        columns: [
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
            {
                data: "last_updated_at",
                render: function (data, type, row) {
                    return moment(data).utc().format("YYYY-MM-DD HH:mm");
                },
            },
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
        ],
        order: [[5, "desc"]],
        lengthMenu: lengthMenu,
        paging: dataTablesPaging,
        pageLength: dataTablesPageLength,
        columnDefs: [createVisibleColumDef(7)],
        initComplete: function () {
            initializeMultiSelectFilters(
                this.api(),
                preliminaryFilterDefinition,
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
                $(row).addClass("active");
            } else if (data["is_important"]) {
                $(row).addClass("warning");
            }
        },
    });

    function updateTimers() {
        table_current.rows().every(function () {
            var d = this.data();
            if (!d["is_passed"]) {
                d["local_time"] = d["date"];
                table_current.row(this).data(d);
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
