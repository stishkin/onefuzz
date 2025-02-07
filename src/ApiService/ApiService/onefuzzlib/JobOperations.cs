﻿using ApiService.OneFuzzLib.Orm;

namespace Microsoft.OneFuzz.Service;

public interface IJobOperations : IStatefulOrm<Job, JobState> {
    Async.Task<Job?> Get(Guid jobId);
    Async.Task OnStart(Job job);
    IAsyncEnumerable<Job> SearchExpired();
    Async.Task Stopping(Job job, ITaskOperations taskOperations);
    IAsyncEnumerable<Job> SearchState(IEnumerable<JobState> states);
    Async.Task StopNeverStartedJobs();
}

public class JobOperations : StatefulOrm<Job, JobState>, IJobOperations {

    public JobOperations(ILogTracer logTracer, IOnefuzzContext context) : base(logTracer, context) {
    }

    public async Async.Task<Job?> Get(Guid jobId) {
        return await QueryAsync($"PartitionKey eq '{jobId}'").FirstOrDefaultAsync();
    }

    public async Async.Task OnStart(Job job) {
        if (job.EndTime == null) {
            await Replace(job with { EndTime = DateTimeOffset.UtcNow + TimeSpan.FromHours(job.Config.Duration) });
        }
    }

    public IAsyncEnumerable<Job> SearchExpired() {
        return QueryAsync(filter: $"end_time lt datetime'{DateTimeOffset.UtcNow.ToString("o")}'");
    }

    public IAsyncEnumerable<Job> SearchState(IEnumerable<JobState> states) {
        var query = Query.EqualAnyEnum("state", states);
        return QueryAsync(filter: query);
    }

    public Async.Task StopNeverStartedJobs() {
        throw new NotImplementedException();
    }

    public async Async.Task Stopping(Job job, ITaskOperations taskOperations) {
        job = job with { State = JobState.Stopping };
        var tasks = await taskOperations.QueryAsync(filter: $"job_id eq '{job.JobId}'").ToListAsync();
        var taskNotStopped = tasks.ToLookup(task => task.State != TaskState.Stopped);

        var notStopped = taskNotStopped[true];
        var stopped = taskNotStopped[false];

        if (notStopped.Any()) {
            foreach (var task in notStopped) {
                await taskOperations.MarkStopping(task);
            }
        } else {
            job = job with { State = JobState.Stopped };
            var taskInfo = stopped.Select(t => new JobTaskStopped(t.TaskId, t.Config.Task.Type, t.Error)).ToList();
            await _context.Events.SendEvent(new EventJobStopped(job.JobId, job.Config, job.UserInfo, taskInfo));
        }

        await Replace(job);

    }
}
