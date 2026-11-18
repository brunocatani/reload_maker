#pragma once

// The collision build worker ROCK would run in game, exercised by Maker.
//
// Thread model: one worker thread, created by the constructor and joined by
// the destructor. The owner (game/UI thread) submits jobs and polls results;
// both queues are guarded by one mutex that is held only to move jobs or
// results, never while a job runs. A job sees the cancel flag of its
// generation: advancing the generation (an equip or attachment change)
// cancels the running job and drops queued ones, and their results never
// reach poll().
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace reloadmaker::collision_worker
{
    using Clock = std::chrono::steady_clock;

    struct Outcome
    {
        std::uint64_t job{};
        std::uint64_t generation{};
        bool succeeded{};
        std::string error;
        Clock::time_point submitted{};
        Clock::time_point started{};
        Clock::time_point finished{};
        // Owned by the submitter's task; returned unchanged.
        std::shared_ptr<void> payload;
    };

    // Runs on the worker thread. Return the payload, or throw to fail the job.
    using Task = std::function<std::shared_ptr<void>(const std::atomic<bool>& cancel)>;

    class Worker
    {
    public:
        Worker();
        ~Worker();
        Worker(const Worker&) = delete;
        Worker& operator=(const Worker&) = delete;

        // Starts a new generation: the running job is cancelled and queued
        // jobs of older generations are dropped. Returns the new generation.
        std::uint64_t advanceGeneration();
        std::uint64_t submit(Task task);
        // Finished jobs of the current generation, oldest first.
        std::vector<Outcome> poll();
        [[nodiscard]] std::size_t pending() const;

    private:
        struct Job
        {
            std::uint64_t id{};
            std::uint64_t generation{};
            Clock::time_point submitted{};
            Task task;
        };
        void run();

        mutable std::mutex _mutex;
        std::condition_variable _wake;
        std::deque<Job> _jobs;
        std::vector<Outcome> _done;
        std::uint64_t _generation{ 1 };
        std::uint64_t _nextJob{ 1 };
        bool _running{ false };
        bool _stopping{ false };
        std::shared_ptr<std::atomic<bool>> _cancel{ std::make_shared<std::atomic<bool>>(false) };
        std::thread _thread;
    };
}
