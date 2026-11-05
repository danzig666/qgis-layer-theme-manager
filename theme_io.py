import json

from qgis.core import QgsMapThemeCollection, QgsProject, Qgis

from .theme_utils import layer_source, layer_source_basename, log, unique_name


FORMAT = "qgis-layer-theme-manager"
VERSION = 1


def _as_list(value):
    if value is None:
        return []
    try:
        return sorted(list(value))
    except Exception:
        try:
            return list(value)
        except Exception:
            return []


def _get_attr(obj, attr, default=None):
    try:
        return getattr(obj, attr)
    except Exception:
        return default


def _layer_info(layer, cache):
    if layer is None:
        return ("", "", "", "")
    layer_id = layer.id()
    info = cache.get(layer_id) if cache is not None else None
    if info is None:
        info = (layer_id, layer.name(), layer_source(layer), layer_source_basename(layer))
        if cache is not None:
            cache[layer_id] = info
    return info


def record_to_dict(name, record, layer_cache=None):
    layer_records = []
    for layer_record in record.layerRecords():
        layer = None
        try:
            layer = layer_record.layer()
        except Exception:
            pass
        layer_id, layer_name, source, basename = _layer_info(layer, layer_cache)
        layer_records.append(
            {
                "layer_id": layer_id,
                "layer_name": layer_name,
                "layer_source": source,
                "layer_source_basename": basename,
                "current_style": _get_attr(layer_record, "currentStyle", ""),
                "using_current_style": bool(_get_attr(layer_record, "usingCurrentStyle", True)),
                "using_legend_items": bool(_get_attr(layer_record, "usingLegendItems", False)),
                "checked_legend_items": _as_list(_get_attr(layer_record, "checkedLegendItems", [])),
                "expanded_legend_items": _as_list(_get_attr(layer_record, "expandedLegendItems", [])),
                "expanded_layer_node": bool(_get_attr(layer_record, "expandedLayerNode", False)),
                "is_visible": bool(_get_attr(layer_record, "isVisible", True)),
            }
        )

    has_expanded = False
    expanded_groups = []
    if hasattr(record, "hasExpandedStateInfo"):
        try:
            has_expanded = bool(record.hasExpandedStateInfo())
            if has_expanded:
                expanded_groups = _as_list(record.expandedGroupNodes())
        except Exception:
            pass

    has_checked = False
    checked_groups = []
    if hasattr(record, "hasCheckedStateInfo"):
        try:
            has_checked = bool(record.hasCheckedStateInfo())
            if has_checked:
                checked_groups = _as_list(record.checkedGroupNodes())
        except Exception:
            pass

    return {
        "name": name,
        "layer_records": layer_records,
        "has_expanded_state_info": has_expanded,
        "expanded_group_nodes": expanded_groups,
        "has_checked_state_info": has_checked,
        "checked_group_nodes": checked_groups,
    }


def export_payload(names, coll, progress_callback=None):
    themes = []
    layer_cache = {}
    total = len(names)
    for index, name in enumerate(names, 1):
        if progress_callback:
            progress_callback(index, total, "Exporting theme '{0}'".format(name))
        if not coll.hasMapTheme(name):
            continue
        themes.append(record_to_dict(name, coll.mapThemeState(name), layer_cache))
    return {"format": FORMAT, "version": VERSION, "themes": themes}


def dumps_payload(payload):
    return json.dumps(payload, indent=2, sort_keys=True)


def loads_payload(text):
    data = json.loads(text)
    if isinstance(data, list):
        data = {"format": FORMAT, "version": VERSION, "themes": data}
    if data.get("format") != FORMAT:
        raise ValueError("This JSON does not look like Layer Theme Manager data.")
    if "themes" not in data or not isinstance(data["themes"], list):
        raise ValueError("No themes were found in the JSON data.")
    return data


def _layer_indexes(project):
    by_name = {}
    by_source = {}
    by_basename = {}
    for layer in project.mapLayers().values():
        by_name.setdefault(layer.name(), []).append(layer)
        source = layer_source(layer)
        if source:
            by_source.setdefault(source, []).append(layer)
        basename = layer_source_basename(layer)
        if basename:
            by_basename.setdefault(basename, []).append(layer)
    return by_name, by_source, by_basename


def _only(values):
    return values[0] if values and len(values) == 1 else None


def _match_layer(info, project, indexes):
    layer_id = info.get("layer_id") or ""
    if layer_id:
        layer = project.mapLayer(layer_id)
        if layer is not None:
            return layer, "id"
    by_name, by_source, by_basename = indexes
    layer = _only(by_name.get(info.get("layer_name") or "", []))
    if layer is not None:
        return layer, "name"
    layer = _only(by_source.get(info.get("layer_source") or "", []))
    if layer is not None:
        return layer, "source"
    layer = _only(by_basename.get(info.get("layer_source_basename") or "", []))
    if layer is not None:
        return layer, "source file"
    return None, None


def _set_attr_safe(obj, attr, value):
    if hasattr(obj, attr):
        try:
            setattr(obj, attr, value)
        except Exception as exc:
            log("Could not set {0} on map theme layer record: {1}".format(attr, exc), getattr(Qgis, "Warning", 1))


def dict_to_record(theme_data, project=None, indexes=None):
    project = project or QgsProject.instance()
    if indexes is None:
        indexes = _layer_indexes(project)
    record = QgsMapThemeCollection.MapThemeRecord()
    records = []
    missing = []
    repaired = []

    for info in theme_data.get("layer_records", []):
        layer, match_method = _match_layer(info, project, indexes)
        if layer is None:
            missing.append(info.get("layer_name") or info.get("layer_id") or "<unknown>")
            continue
        if match_method != "id":
            repaired.append("{0} ({1})".format(layer.name(), match_method))
        layer_record = QgsMapThemeCollection.MapThemeLayerRecord(layer)
        _set_attr_safe(layer_record, "currentStyle", info.get("current_style") or "")
        _set_attr_safe(layer_record, "usingCurrentStyle", bool(info.get("using_current_style", True)))
        _set_attr_safe(layer_record, "usingLegendItems", bool(info.get("using_legend_items", False)))
        _set_attr_safe(layer_record, "checkedLegendItems", set(info.get("checked_legend_items") or []))
        _set_attr_safe(layer_record, "expandedLegendItems", set(info.get("expanded_legend_items") or []))
        _set_attr_safe(layer_record, "expandedLayerNode", bool(info.get("expanded_layer_node", False)))
        _set_attr_safe(layer_record, "isVisible", bool(info.get("is_visible", True)))
        records.append(layer_record)

    record.setLayerRecords(records)

    if hasattr(record, "setHasExpandedStateInfo"):
        try:
            record.setHasExpandedStateInfo(bool(theme_data.get("has_expanded_state_info", False)))
            if hasattr(record, "setExpandedGroupNodes"):
                record.setExpandedGroupNodes(set(theme_data.get("expanded_group_nodes") or []))
        except Exception:
            pass

    if hasattr(record, "setHasCheckedStateInfo"):
        try:
            record.setHasCheckedStateInfo(bool(theme_data.get("has_checked_state_info", False)))
            if hasattr(record, "setCheckedGroupNodes"):
                record.setCheckedGroupNodes(set(theme_data.get("checked_group_nodes") or []))
        except Exception:
            pass

    return record, missing, repaired


def import_payload(payload, coll, project=None, progress_callback=None):
    project = project or QgsProject.instance()
    if any(not isinstance(t, dict) for t in payload.get("themes", [])):
        raise ValueError("Invalid theme entry in JSON data.")
    existing = set(coll.mapThemes())
    imported = []
    renamed = []
    missing = {}
    repaired = {}

    themes = payload.get("themes", [])
    indexes = _layer_indexes(project)
    total = len(themes)
    for index, theme_data in enumerate(themes, 1):
        original_name = (theme_data.get("name") or "Imported theme").strip() or "Imported theme"
        if progress_callback:
            progress_callback(index, total, "Importing theme '{0}'".format(original_name))
        target_name = unique_name(original_name, existing)
        existing.add(target_name)
        record, theme_missing, theme_repaired = dict_to_record(theme_data, project, indexes)
        coll.insert(target_name, record)
        try:
            project.setDirty(True)
        except Exception:
            pass
        imported.append(target_name)
        if target_name != original_name:
            renamed.append((original_name, target_name))
        if theme_missing:
            missing[target_name] = theme_missing
        if theme_repaired:
            repaired[target_name] = theme_repaired

    return {
        "imported": imported,
        "renamed": renamed,
        "missing": missing,
        "repaired": repaired,
    }
