$(document).ready(function () {
    const pastedTimer = document.querySelector('[name="pasted_timer"]');
    const ownerField = document.querySelector('[name="owner_name"]');
    const structureTypeField = document.querySelector('[name="structure_type_2"]');

    if (!pastedTimer || !ownerField || !structureTypeField) {
        return;
    }

    const skyhookTypeId = structureTypeField.dataset.skyhookTypeId;
    let autoFilledOwner = null;
    let autoFilledType = false;

    function clearAutoFilledValues() {
        if (autoFilledOwner !== null && ownerField.value === autoFilledOwner) {
            ownerField.value = "";
        }
        if (autoFilledType && structureTypeField.value === skyhookTypeId) {
            $(structureTypeField).val(null).trigger("change");
        }
        autoFilledOwner = null;
        autoFilledType = false;
    }

    function fillFromPastedTimer() {
        const header = pastedTimer.value
            .split(/\r?\n/)
            .map((line) => line.trim())
            .find((line) => line.length > 0);
        const skyhookMatch = header && header.match(
            /^Orbital Skyhook\s+\((.+)\s+([^\s]+)\)\s+\[(.+)\]$/i
        );

        if (!skyhookMatch) {
            clearAutoFilledValues();
            return;
        }

        const owner = skyhookMatch[3].trim();
        if (!owner) {
            clearAutoFilledValues();
            return;
        }

        ownerField.value = owner;
        autoFilledOwner = owner;

        let skyhookOption = Array.from(structureTypeField.options).find(
            (option) => option.value === skyhookTypeId
        );
        if (!skyhookOption) {
            skyhookOption = new Option("Orbital Skyhook", skyhookTypeId, true, true);
            structureTypeField.add(skyhookOption);
        } else {
            skyhookOption.text = "Orbital Skyhook";
        }
        $(structureTypeField).val(skyhookTypeId).trigger("change");
        autoFilledType = true;
    }

    pastedTimer.addEventListener("input", fillFromPastedTimer);
    fillFromPastedTimer();
});
