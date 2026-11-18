#pragma once
#include "ReloadRecipe.h"
#include <QMap>
namespace reloadmaker {
struct MagazineSetup {
    QString recipeId,motion{"preset"},movementId;
    QString sourceClip,sourceBaseline; // Optional native insertion source, independent of loadout animation keywords.
    int emptyTrack{-1},loadedFrame{},emptyFrame{};
    bool sampledPayload{};
    float entrySeconds{};
    float seatSeconds{-1}; // Loading ends here, before a round is culled or its carrier closes.
    QMap<QString,QString> pieces; // Complete native shape path -> body, cartridge or spare.
    struct Chamber {int track{-1};float seatSeconds{};};
    QVector<Chamber> chambers; // Each physical socket, sampled after its own insertion.
    QVector<int> loadingFollowers; // Lifter/gate tracks driven by each inserted round.
    QJsonArray trackAnchors; // The node of each track index (recipeTrackAnchors).
};
bool readMagazineSetup(const QJsonObject&,MagazineSetup&,QString&);
QJsonObject magazineSetupJson(const MagazineSetup&);
}
