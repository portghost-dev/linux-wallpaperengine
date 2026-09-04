#pragma once

#include <condition_variable>
#include <deque>
#include <functional>
#include <mutex>
#include <thread>
#include <vector>

namespace WallpaperEngine::Render::Utils {
/**
 * A fixed set of worker threads that runs a batch of independent tasks and waits for all of
 * them. Used for per-frame CPU work that touches no GL state, such as particle simulation.
 */
class WorkPool {
  public:
    static WorkPool& instance ();

    /** Runs every task on the workers and returns once all of them have finished. */
    void run (std::vector<std::function<void ()>> tasks);

    [[nodiscard]] size_t workerCount () const { return m_threads.size (); }

  private:
    WorkPool ();
    ~WorkPool ();

    void worker ();

    std::vector<std::thread> m_threads;
    std::mutex m_mutex;
    std::condition_variable m_wake;
    std::condition_variable m_done;
    std::deque<std::function<void ()>> m_queue;
    size_t m_pending = 0;
    bool m_stop = false;
};
} // namespace WallpaperEngine::Render::Utils
