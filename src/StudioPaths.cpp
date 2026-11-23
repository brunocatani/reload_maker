#include "StudioPaths.h"
#include <QCoreApplication>
#include <QDir>
#include <QVariant>
namespace reloadmaker {
QString studioRoot() {
    return QCoreApplication::instance()->property("studioRoot").toString();
}
QString studioDataPath(const QString& relative) {
    return QDir(studioRoot()).filePath(relative);
}
QString workspaceStoredPath(const QString& path) {
    if (path.isEmpty()) {
        return path;
    }
    const auto root = QDir::cleanPath(studioRoot());
    const auto clean = QDir::cleanPath(path);
    if (clean.compare(root, Qt::CaseInsensitive) == 0) {
        return QStringLiteral(".");
    }
    if (!root.isEmpty() && clean.startsWith(root + QLatin1Char('/'), Qt::CaseInsensitive)) {
        return clean.mid(root.size() + 1);
    }
    return path;
}
QString workspaceResolvedPath(const QString& stored) {
    if (stored.isEmpty() || QDir::isAbsolutePath(stored)) {
        return stored;
    }
    return QDir::cleanPath(QDir(studioRoot()).filePath(stored));
}
}
