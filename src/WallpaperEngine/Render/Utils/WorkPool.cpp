#include "WorkPool.h"

#include "WallpaperEngine/Logging/Log.h"

#include <algorithm>
#include <exception>

using namespace WallpaperEngine::Render::Utils;

WorkPool& WorkPool::instance () {
    static WorkPool pool;
    return pool;
}

WorkPool::WorkPool () {
    const unsigned cores = std::thread::hardware_concurrency ();
    const unsigned count = std::max (1u, std::min (8u, cores > 2 ? cores - 2 : 1u));

    for (unsigned i = 0; i < count; i++) {
	m_threads.emplace_back ([this] { this->worker (); });
    }
}

WorkPool::~WorkPool () {
    {
	std::lock_guard lock (m_mutex);
	m_stop = true;
    }

    m_wake.notify_all ();

    for (auto& thread : m_threads) {
	thread.join ();
    }
}

void WorkPool::run (std::vector<std::function<void ()>> tasks) {
    if (tasks.empty ()) {
	return;
    }

    std::unique_lock lock (m_mutex);

    for (auto& task : tasks) {
	m_queue.push_back (std::move (task));
    }

    m_pending += tasks.size ();
    m_wake.notify_all ();
    m_done.wait (lock, [this] { return m_pending == 0; });
}

void WorkPool::worker () {
    std::unique_lock lock (m_mutex);

    while (true) {
	m_wake.wait (lock, [this] { return m_stop || !m_queue.empty (); });

	if (m_stop && m_queue.empty ()) {
	    return;
	}

	std::function<void ()> task = std::move (m_queue.front ());
	m_queue.pop_front ();
	lock.unlock ();

	try {
	    task ();
	} catch (const std::exception& e) {
	    sLog.error ("Work pool task failed: ", e.what ());
	}

	lock.lock ();

	if (--m_pending == 0) {
	    m_done.notify_all ();
	}
    }
}
