#pragma once
#include <QString>
namespace reloadmaker {
QString studioRoot();
QString studioDataPath(const QString& relative);
// Saved data names a file inside the workspace by its path from the workspace
// root, so the workspace can be moved or renamed without losing its sources.
// A path outside the workspace is stored as it is. Reading accepts both forms.
QString workspaceStoredPath(const QString& path);
QString workspaceResolvedPath(const QString& stored);
}
