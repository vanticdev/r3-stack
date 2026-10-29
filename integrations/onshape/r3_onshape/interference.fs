function(context is Context, queries) {
    const parts = evaluateQuery(context, qAllModifiableSolidBodies());
    var out = [];
    for (var i = 0; i < size(parts); i += 1) {
        for (var j = i + 1; j < size(parts); j += 1) {
            const d = evDistance(context, {"side0" : parts[i], "side1" : parts[j]}).distance;
            if (d < 0.003 * meter) {
                var vol = -1;
                try silent {
                    const iid = makeId("ix" ~ i ~ "_" ~ j);
                    opBoolean(context, iid, {"tools" : qUnion([parts[i], parts[j]]), "operationType" : BooleanOperationType.INTERSECTION, "keepTools" : true});
                    vol = evVolume(context, {"entities" : qCreatedBy(iid, EntityType.BODY)}) / meter ^ 3;
                }
                out = append(out, [getProperty(context, {"entity" : parts[i], "propertyType" : PropertyType.NAME}),
                                   getProperty(context, {"entity" : parts[j], "propertyType" : PropertyType.NAME}), d / meter, vol]);
            }
        }
    }
    return out;
}
