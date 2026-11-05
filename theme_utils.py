import os

from qgis.core import QgsLayerTreeGroup, QgsLayerTreeLayer, QgsLayerTreeModel, QgsMapThemeCollection, QgsMessageLog, QgsProject, Qgis


LOG_TAG = "Layer Theme Manager"


def log(message, level=None):
    if level is None:
        level = getattr(Qgis, "Info", 0)
    QgsMessageLog.logMessage(str(message), LOG_TAG, level)


def project():
    return QgsProject.instance()


def collection():
    return project().mapThemeCollection()


def map_theme_names(coll=None):
    coll = coll or collection()
    try:
        return list(coll.mapThemes())
    except Exception as exc:
        log("Could not read map themes: {0}".format(exc), getattr(Qgis, "Warning", 1))
        return []


def has_theme(name, coll=None):
    coll = coll or collection()
    try:
        return bool(coll.hasMapTheme(name))
    except Exception:
        return name in map_theme_names(coll)


def unique_name(base, existing=None):
    base = (base or "Theme").strip() or "Theme"
    existing = set(existing if existing is not None else map_theme_names())
    if base not in existing:
        return base
    index = 2
    while True:
        candidate = "{0} {1}".format(base, index)
        if candidate not in existing:
            return candidate
        index += 1


def duplicate_name(name, existing=None, pattern=None):
    existing = set(existing if existing is not None else map_theme_names())
    pattern = (pattern or "{name} copy").strip() or "{name} copy"
    try:
        first = pattern.format(name=name)
    except Exception:
        first = "{0} copy".format(name)
    if first not in existing:
        return first
    index = 2
    while True:
        try:
            candidate = pattern.format(name=name, n=index)
        except Exception:
            candidate = "{0} {1}".format(first, index)
        if "{n}" not in pattern:
            candidate = "{0} {1}".format(first, index)
        if candidate not in existing:
            return candidate
        index += 1


def clone_layer_record(record):
    try:
        return QgsMapThemeCollection.MapThemeLayerRecord(record)
    except Exception:
        layer = None
        try:
            layer = record.layer()
        except Exception:
            pass
        new_record = QgsMapThemeCollection.MapThemeLayerRecord(layer)
        for attr in (
            "currentStyle",
            "usingCurrentStyle",
            "usingLegendItems",
            "checkedLegendItems",
            "expandedLegendItems",
            "expandedLayerNode",
            "isVisible",
        ):
            if hasattr(record, attr):
                try:
                    setattr(new_record, attr, getattr(record, attr))
                except Exception:
                    pass
        return new_record


def clone_record(record):
    new_record = QgsMapThemeCollection.MapThemeRecord()
    try:
        new_record.setLayerRecords([clone_layer_record(r) for r in record.layerRecords()])
    except Exception:
        pass

    if hasattr(record, "hasExpandedStateInfo") and hasattr(new_record, "setHasExpandedStateInfo"):
        try:
            new_record.setHasExpandedStateInfo(record.hasExpandedStateInfo())
            if record.hasExpandedStateInfo() and hasattr(new_record, "setExpandedGroupNodes"):
                new_record.setExpandedGroupNodes(record.expandedGroupNodes())
        except Exception:
            pass

    if hasattr(record, "hasCheckedStateInfo") and hasattr(new_record, "setHasCheckedStateInfo"):
        try:
            new_record.setHasCheckedStateInfo(record.hasCheckedStateInfo())
            if record.hasCheckedStateInfo() and hasattr(new_record, "setCheckedGroupNodes"):
                new_record.setCheckedGroupNodes(record.checkedGroupNodes())
        except Exception:
            pass

    return new_record


def rename_theme(old_name, new_name, coll=None):
    coll = coll or collection()
    if hasattr(coll, "renameMapTheme"):
        try:
            if coll.renameMapTheme(old_name, new_name):
                mark_project_dirty()
                return True
        except Exception as exc:
            log("renameMapTheme failed, using fallback: {0}".format(exc))

    record = clone_record(coll.mapThemeState(old_name))
    coll.insert(new_name, record)
    coll.removeMapTheme(old_name)
    mark_project_dirty()
    return True


def insert_theme(name, record, coll=None):
    coll = coll or collection()
    coll.insert(name, clone_record(record))
    mark_project_dirty()


def update_theme(name, record, coll=None):
    coll = coll or collection()
    if hasattr(coll, "update"):
        coll.update(name, clone_record(record))
    else:
        if has_theme(name, coll):
            coll.removeMapTheme(name)
        coll.insert(name, clone_record(record))
    mark_project_dirty()


def remove_theme(name, coll=None):
    coll = coll or collection()
    if has_theme(name, coll):
        coll.removeMapTheme(name)
        mark_project_dirty()
        return True
    return False


def layer_tree_model(iface):
    """Return the real QgsLayerTreeModel, unwrapping proxy models used by some QGIS builds."""
    model = None
    try:
        model = iface.layerTreeView().model()
    except Exception:
        return None

    seen = set()
    while model is not None and id(model) not in seen:
        seen.add(id(model))
        if isinstance(model, QgsLayerTreeModel):
            return model
        source_model = getattr(model, "sourceModel", None)
        if source_model is None:
            break
        try:
            model = source_model()
        except Exception:
            break
    return model if isinstance(model, QgsLayerTreeModel) else None


def current_state_record(iface):
    root = QgsProject.instance().layerTreeRoot()
    model = layer_tree_model(iface)
    if model is None:
        # Passing None crashes QGIS; use a temporary model as a safe fallback.
        model = QgsLayerTreeModel(root)
        try:
            return QgsMapThemeCollection.createThemeFromCurrentState(root, model)
        finally:
            model.deleteLater()
    return QgsMapThemeCollection.createThemeFromCurrentState(root, model)


def apply_theme_to_project(name, iface, coll=None):
    coll = coll or collection()
    root = QgsProject.instance().layerTreeRoot()
    model = layer_tree_model(iface)
    if model is None:
        model = QgsLayerTreeModel(root)
        try:
            coll.applyTheme(name, root, model)
        finally:
            model.deleteLater()
    else:
        coll.applyTheme(name, root, model)
    try:
        iface.layerTreeView().refreshLayerSymbology(None)
    except Exception:
        pass
    try:
        iface.mapCanvas().refresh()
    except Exception:
        pass


def layer_tree_index(root=None):
    """Flatten the project layer tree once.

    Returns a list of ``(layer_id, ancestor_group_paths)`` tuples in tree order.
    Group paths use the same ``a/b/c`` format as QgsMapThemeCollection.
    """
    root = root or QgsProject.instance().layerTreeRoot()
    result = []

    def walk(node, path, ancestors):
        for child in node.children():
            if isinstance(child, QgsLayerTreeLayer):
                result.append((child.layerId(), ancestors))
            elif isinstance(child, QgsLayerTreeGroup):
                child_path = child.name() if not path else "{0}/{1}".format(path, child.name())
                walk(child, child_path, ancestors + (child_path,))

    walk(root, "", ())
    return result


def record_checked_groups(record):
    """Return the set of checked group paths, or None when the record has no group info."""
    try:
        if hasattr(record, "hasCheckedStateInfo") and record.hasCheckedStateInfo():
            return set(record.checkedGroupNodes())
    except Exception:
        pass
    return None


def record_visible_ids(record):
    ids = set()
    try:
        records = record.layerRecords()
    except Exception:
        return ids
    for layer_record in records:
        try:
            layer = layer_record.layer()
        except Exception:
            layer = None
        if layer is None:
            continue
        if not getattr(layer_record, "isVisible", True):
            continue
        ids.add(layer.id())
    return ids


def theme_visible_layer_ids(record, tree_index=None, record_ids=None):
    """Layers that are effectively visible when the theme is applied.

    Pure set arithmetic over the theme record; much faster than cloning the
    layer tree and applying the theme to a temporary QgsLayerTreeModel (which
    builds legend nodes for every layer).
    """
    if tree_index is None:
        tree_index = layer_tree_index()
    if record_ids is None:
        record_ids = record_visible_ids(record)
    checked_groups = record_checked_groups(record)
    visible = set()
    for layer_id, ancestors in tree_index:
        if layer_id not in record_ids:
            continue
        if checked_groups is not None and any(path not in checked_groups for path in ancestors):
            continue
        visible.add(layer_id)
    return visible


def visible_layer_ids_for_theme(name, iface=None, coll=None, tree_index=None):
    coll = coll or collection()
    try:
        return theme_visible_layer_ids(coll.mapThemeState(name), tree_index)
    except Exception as exc:
        log("Could not evaluate theme '{0}': {1}".format(name, exc), getattr(Qgis, "Warning", 1))
        return set()


def current_visible_layer_ids(root=None):
    checked = set()
    collect_checked_layer_ids(root or QgsProject.instance().layerTreeRoot(), True, checked)
    return checked


def theme_summary(name, coll, tree_index=None):
    """Collect everything the main table needs about one theme in a single pass."""
    info = {
        "name": name,
        "layer_ids": [],
        "layer_names": [],
        "legend": 0,
        "styles": 0,
        "missing": 0,
        "hidden": 0,
        "visible_ids": frozenset(),
        "checked_groups": None,
        "error": "",
    }
    try:
        record = coll.mapThemeState(name)
        layer_records = record.layerRecords()
    except Exception as exc:
        info["error"] = str(exc)
        return info
    ids = set()
    for layer_record in layer_records:
        try:
            layer = layer_record.layer()
        except Exception:
            layer = None
        if layer is None:
            info["missing"] += 1
            continue
        if not getattr(layer_record, "isVisible", True):
            info["hidden"] += 1
            continue
        layer_id = layer.id()
        ids.add(layer_id)
        info["layer_ids"].append(layer_id)
        info["layer_names"].append(layer.name())
        try:
            if layer_record.usingLegendItems:
                info["legend"] += len(layer_record.checkedLegendItems or [])
        except Exception:
            pass
        # QGIS stores a style name for every layer; it only matters when the
        # layer actually has more than one style to choose from.
        try:
            if layer_record.usingCurrentStyle and len(layer.styleManager().styles()) > 1:
                info["styles"] += 1
        except Exception:
            pass
    info["checked_groups"] = record_checked_groups(record)
    info["visible_ids"] = frozenset(theme_visible_layer_ids(record, tree_index, ids))
    return info


def collect_checked_layer_ids(node, parent_visible, checked):
    for child in node.children():
        try:
            node_checked = child.itemVisibilityChecked()
        except Exception:
            node_checked = True
        visible = parent_visible and node_checked
        if isinstance(child, QgsLayerTreeLayer):
            if visible and child.layer() is not None:
                checked.add(child.layerId())
        elif isinstance(child, QgsLayerTreeGroup):
            collect_checked_layer_ids(child, visible, checked)


def mark_project_dirty():
    try:
        QgsProject.instance().setDirty(True)
    except Exception:
        pass


def layer_source_basename(layer):
    try:
        source = layer.source()
    except Exception:
        return ""
    path = source.split("|", 1)[0]
    return os.path.basename(path)


def layer_source(layer):
    try:
        return layer.source()
    except Exception:
        return ""


def record_by_layer_id(record):
    result = {}
    try:
        records = record.layerRecords()
    except Exception:
        return result
    for layer_record in records:
        try:
            layer = layer_record.layer()
        except Exception:
            layer = None
        if layer is not None:
            result[layer.id()] = layer_record
    return result
