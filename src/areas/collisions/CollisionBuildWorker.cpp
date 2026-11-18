#include "areas/collisions/CollisionBuildWorker.h"

#include <exception>

namespace reloadmaker::collision_worker
{
    Worker::Worker() : _thread([this] { run(); }) {}

    Worker::~Worker()
    {
        {
            const std::scoped_lock lock(_mutex);
            _stopping = true;
            _cancel->store(true);
            _jobs.clear();
        }
        _wake.notify_all();
        _thread.join();
    }

    std::uint64_t Worker::advanceGeneration()
    {
        const std::scoped_lock lock(_mutex);
        ++_generation;
        // The running job keeps the old flag object; new jobs get a fresh one.
        _cancel->store(true);
        _cancel = std::make_shared<std::atomic<bool>>(false);
        std::erase_if(_jobs, [this](const Job& job) { return job.generation != _generation; });
        std::erase_if(_done, [this](const Outcome& outcome) { return outcome.generation != _generation; });
        return _generation;
    }

    std::uint64_t Worker::submit(Task task)
    {
        std::uint64_t id{};
        {
            const std::scoped_lock lock(_mutex);
            id = _nextJob++;
            _jobs.push_back({ id, _generation, Clock::now(), std::move(task) });
        }
        _wake.notify_one();
        return id;
    }

    std::vector<Outcome> Worker::poll()
    {
        const std::scoped_lock lock(_mutex);
        std::vector<Outcome> out;
        out.swap(_done);
        return out;
    }

    std::size_t Worker::pending() const
    {
        const std::scoped_lock lock(_mutex);
        return _jobs.size() + (_running ? 1 : 0);
    }

    void Worker::run()
    {
        for (;;) {
            Job job;
            std::shared_ptr<std::atomic<bool>> cancel;
            {
                std::unique_lock lock(_mutex);
                _wake.wait(lock, [this] { return _stopping || !_jobs.empty(); });
                if (_stopping) {
                    return;
                }
                job = std::move(_jobs.front());
                _jobs.pop_front();
                cancel = _cancel;
                _running = true;
            }
            Outcome outcome{ .job = job.id, .generation = job.generation, .submitted = job.submitted, .started = Clock::now() };
            try {
                outcome.payload = job.task(*cancel);
                outcome.succeeded = !cancel->load();
            } catch (const std::exception& e) {
                outcome.error = e.what();
            } catch (...) {
                outcome.error = "collision job failed";
            }
            outcome.finished = Clock::now();
            const std::scoped_lock lock(_mutex);
            _running = false;
            if (job.generation == _generation) {
                _done.push_back(std::move(outcome));
            }
        }
    }
}
