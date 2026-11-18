// Pack and asset jobs: one bundled product-tool process at a time exports ESP
// projects, commits or deletes their saved versions, validates or inspects
// packs, or prepares and cooks world models. Every dialog and command shares
// the one job state.
#include "ReloadMakerController.h"
#include "StudioPaths.h"
#include "StudioWindow.h"
#include "commands/CommandSupport.h"

#include <QCoreApplication>
#include <QDir>
#include <QDialog>
#include <QGridLayout>
#include <QLabel>
#include <QPixmap>
#include <QScrollArea>
#include <QVBoxLayout>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QMap>
#include <QProcess>
#include <QSaveFile>
#include <QSet>
#include <QUuid>
#include <memory>
#include <algorithm>

namespace reloadmaker
{
    using commands::require;

    // state; cancel; open/close the export window; export ESP projects; or
    // cook or prepare a world model.
    QJsonObject ReloadMakerController::packCommand(const QJsonObject& request)
    {
        const auto operation = request.value("operation").toString("state");
        if (operation == "state") {
            const auto id = request["id"].toString();
            if (!id.isEmpty() && id != _packJob["id"].toString()) {
                require(_packJobHistory.contains(id), "Unknown or expired job ID: " + id);
                return _packJobHistory.value(id);
            }
            return _packJob.isEmpty() ? QJsonObject{ { "phase", "idle" } } : _packJob;
        }
        if (operation == "cancel") {
            require(request["id"].toString().isEmpty() || request["id"] == _packJob["id"], "Cancellation names another job");
            cancelPackJob();
            return _packJob;
        }
        require(!_packProcess && !_saveAll && !espBusy(), "A save or pack job is already running");
        if(operation=="prepare_pair"||operation=="review_magazine") {
            require(currentWeapon()&&_simulationRig&&_magazineRail,"Prepare the selected magazine first");
            QString python,id;auto arguments=packToolArguments(python,id);
            const auto folder=studioDataPath("local/tmp/part-job-"+id);require(QDir().mkpath(folder),"Cannot create part-job folder");
            auto authoring=projectRecord();QString error;require(appendProjectDrafts(authoring,error),error);
            const auto output=request["output"].toString(studioDataPath((operation=="prepare_pair"?"workspace/world-models/":"evidence/reviews/")+id));
            const auto path=folder+"/request.json";
            const auto record=weaponRecordKey();
            commands::writeFileAtomically(path,QJsonDocument(QJsonObject{{"plugin",record.section(':',0,0)},
                {"weapons",QJsonObject{{record,authoring}}},{"operation",operation},{"output",output}}).toJson());
            arguments<<"capture"<<"--request"<<path;
            startPackProcess(python,arguments,{{"id",id},{"operation",operation},{"phase","running"},{"weapon",record},
                {"magazine",simulationMagazineAnchor().ownerRecord},{"startedAuthoring",authoredPart(authoring)}});
            return _packJob;
        }
        if (operation == "close") {
            if(auto* dialog=_window->findChild<QDialog*>("ReloadExportDialog"))dialog->close();
            return {{"ok",true},{"phase","closed"}};
        }
        if (operation == "open") {
            const auto scope = request["scope"].toString();
            QMetaObject::invokeMethod(this,[this,scope]{showPackBuilder(scope=="everything"?ExportScope::Everything:scope=="esp"?ExportScope::OpenEsp:ExportScope::Chosen);},Qt::QueuedConnection);
            return {{"ok",true},{"phase","dialog"}};
        }
        if (operation == "export") {
            return espExport(request);
        }
        require(operation == "cook" || operation == "prepare", "Unknown pack operation: use state, cancel, open, close, export, cook or prepare");
        require(QFileInfo(request["job"].toString()).isFile() && !request["output"].toString().isEmpty(), "Select an asset job and a finished NIF path");
        QString python;
        QString id;
        auto arguments = packToolArguments(python, id);
        arguments << (operation == "cook" ? "cook-object" : "prepare-object") << "--job" << request["job"].toString() << "--output" << request["output"].toString();
        startPackProcess(python, arguments, { { "id", id }, { "phase", "running" }, { "operation", operation } });
        return _packJob;
    }

    // The bundled tool's interpreter and its leading arguments, with a new
    // job ID (unless one is given) and cancel file.
    QStringList ReloadMakerController::packToolArguments(QString& python, QString& id)
    {
        const QDir application(QCoreApplication::applicationDirPath());
        python = application.filePath("runtime/python/python.exe");
        const auto entry = application.filePath("tools/product_cli.py");
        require(QFileInfo::exists(python) && QFileInfo::exists(entry), "The bundled object builder is missing. Install the complete Reload Maker tool package.");
        if (id.isEmpty()) {
            id = QUuid::createUuid().toString(QUuid::WithoutBraces);
        }
        _packCancelFile = studioDataPath("local/tmp/pack-" + id + ".cancel");
        return { "-B", "-I", entry, "--workspace", studioRoot(), "--cancel-file", _packCancelFile, "--progress" };
    }

    // The running tool stops at its next safe point once the cancel file
    // exists; a capturing save stops after its current step.
    void ReloadMakerController::cancelPackJob()
    {
        // A save between two weapons' captures has nothing running to stop.
        if (_espSave && !_saveAll && !_packProcess) {
            finishEspSave(false, QJsonObject{ { "error", "Save cancelled; nothing was written." }, { "cancelled", true } });
            return;
        }
        if (_saveAll) {
            _saveAll->cancelled = true;
            _packJob["phase"] = "cancelling";
            emit packJobChanged();
            return;
        }
        if (!_packProcess) {
            return;
        }
        QFile cancel(_packCancelFile);
        require(cancel.open(QIODevice::WriteOnly), "Cannot request cancellation");
        cancel.close();
        _packJob["phase"] = "cancelling";
        emit packJobChanged();
    }

    // Consume progress records as they arrive, retaining only the final result
    // and the incomplete line. The worker remains the owner of its operation.
    void ReloadMakerController::startPackProcess(const QString& python, const QStringList& arguments, const QJsonObject& job)
    {
        auto* process = new QProcess(this);
        _packProcess = process;
        process->setProgram(python);
        process->setArguments(arguments);
        process->setWorkingDirectory(studioRoot());
        _packJob = job;
        const auto startedAuthoring=_packJob.take("startedAuthoring").toObject();
        auto output = std::make_shared<QByteArray>();
        auto finalResult = std::make_shared<QJsonObject>();
        const auto collect = [this, process, output, finalResult] {
            output->append(process->readAllStandardOutput());
            qsizetype newline;
            while ((newline = output->indexOf('\n')) >= 0) {
                const auto event = QJsonDocument::fromJson(output->left(newline)).object();
                output->remove(0, newline + 1);
                if (event["event"] == "progress") {
                    _packJob["progress"] = event;
                    _packJob["stage"] = event["stage"];
                    emit packJobChanged();
                } else if (!event.isEmpty()) *finalResult = event;
            }
        };
        connect(process, &QProcess::readyReadStandardOutput, this, collect);
        connect(process, &QProcess::errorOccurred, this, [this, process](QProcess::ProcessError error) {
            if (error != QProcess::FailedToStart) {
                return;
            }
            _packJob["phase"] = "failed";
            _packJob["error"] = process->errorString();
            _packProcess = nullptr;
            process->deleteLater();
            if (_packJob["operation"] == "library-save" || _packJob["operation"] == "library-capture") {
                finishEspSave(false, QJsonObject{ { "error", _packJob["error"] } });
            }
            emit packJobChanged();
        });
        connect(process, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this, [this, process, output, finalResult, collect, startedAuthoring](int code, QProcess::ExitStatus status) {
            collect();
            const auto tail = QJsonDocument::fromJson(output->trimmed()).object();
            const auto result = tail.isEmpty() ? *finalResult : tail;
            const bool success = code == 0 && status == QProcess::NormalExit && result["ok"].toBool();
            const bool cancelled = !success && _packJob["phase"] == "cancelling";
            _packJob["phase"] = success ? "complete" : cancelled ? "cancelled" : "failed";
            _packJob["result"] = result;
            if (!success) {
                _packJob["error"] = result.value("error").toString(QString::fromUtf8(process->readAllStandardError()).right(8192));
            }
            QFile::remove(_packCancelFile);
            QFile::remove(studioDataPath("local/tmp/pack-" + _packJob["id"].toString() + ".loaded.json"));
            _packProcess = nullptr;
            process->deleteLater();
            const auto operation = _packJob["operation"].toString();
            if (operation == "library-capture") {
                finishIsolatedCapture(success, success ? result : QJsonObject{ { "error", _packJob["error"] }, { "cancelled", cancelled } });
            } else if (operation == "library-save") {
                finishEspSave(success, success ? result : QJsonObject{ { "error", _packJob["error"] }, { "cancelled", cancelled } });
            } else if (operation == "library-delete") {
                finishEspDelete(success, success ? result : QJsonObject{ { "error", _packJob["error"] } });
            } else if(operation=="prepare_pair"&&success) {
                // Files remain usable after an external edit, but the completed
                // job must never replace a different document's assignments.
                const bool unchanged=_packJob["weapon"].toString()==weaponRecordKey()&&startedAuthoring==authoredRecord();
                auto delivered=result;
                if(unchanged) {
                    try {assignMagazineModels({{"magazine",_packJob["magazine"]},{"full",result["full"]},{"empty",result["empty"]}});delivered["assigned"]=true;}
                    catch(const std::exception& error){delivered["assigned"]=false;delivered["assignmentNote"]=QString::fromUtf8(error.what());}
                } else {delivered["assigned"]=false;delivered["assignmentNote"]="The authoring changed while the models were being prepared; existing assignments were kept.";}
                _packJob["result"]=delivered;
                setStatus(delivered["assigned"].toBool()?QStringLiteral("Full and empty models assigned. Save to keep the changes."):delivered["assignmentNote"].toString(),false);
            } else if(operation=="review_magazine"&&success) {
                auto* dialog=new QDialog(_window);dialog->setAttribute(Qt::WA_DeleteOnClose);dialog->setWindowTitle("Magazine hand fit review");dialog->resize(1040,720);
                auto* layout=new QVBoxLayout(dialog);auto* scroll=new QScrollArea(dialog);scroll->setWidgetResizable(true);
                auto* body=new QWidget(scroll);auto* grid=new QGridLayout(body);
                const QStringList angles{"side","top","iso"};
                for(const auto& value:result["views"].toArray()) {
                    const auto view=value.toObject();auto* cell=new QWidget(body);auto* content=new QVBoxLayout(cell);
                    auto* caption=new QLabel(view["hand"].toString()+" · "+view["view"].toString(),cell);caption->setTextFormat(Qt::PlainText);content->addWidget(caption);
                    auto* image=new QLabel(cell);image->setPixmap(QPixmap(view["path"].toString()).scaled(470,330,Qt::KeepAspectRatio,Qt::SmoothTransformation));content->addWidget(image);
                    grid->addWidget(cell,std::max(0,int(angles.indexOf(view["view"].toString()))),view["hand"]=="right"?1:0);
                }
                scroll->setWidget(body);layout->addWidget(scroll);dialog->show();
            }
            if (operation.startsWith("library-")) {
                _espIndexCache.clear();
                updatePackCoverage();
            }
            emit packJobChanged();
        });
        process->start();
        emit packJobChanged();
    }

}
