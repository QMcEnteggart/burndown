#!/usr/bin/env python
"""
org-server.py	-- Provide HTTP access to project data in the supplied Git repository

    org-server.py <repo> <project> ...

api/projects

    Returns JSON describing all projects available.

api/data/<project>/<time-style>

   Returns <project> history from the org data in git, with the given time style:

       effort		Data is aggregated for periods of effort time elapsed
       elapsed		Data is aggregated for periods of calendar time elapsed
       sprint           Data is aggregated for each sprint elapsed

"""
from __future__ import with_statement

import argparse
import cgi
import copy
import datetime
try:
    import json
except:
    import simplejson as json
import logging
import math
import re
import socket
import string
import os
import sys
import textwrap
import time

import git		# modules from site-packages

from mathdict import *	# modules local to project

global day_seconds
day_seconds		= 8*60*60 # Default day is 8 hours

# For compatibility with 2.5
# Lifted from:
#     http://stackoverflow.com/questions/1716428/def-next-for-python-pre-2-6-instead-of-object-next-method/1716464#1716464
#     https://github.com/mikeboers/PyHAML/commit/45db152f9c8754ef3f8f249f3e68114c83f8580d

try:
    next
except NameError:
    class Throw(object): pass
    throw = Throw() # easy sentinel hack
    
    def next(iterator, default=throw):
        """next(iterator[, default])
    
        Return the next item from the iterator. If default is given
        and the iterator is exhausted, it is returned instead of
        raising StopIteration.
        """
        try:
            iternext = iterator.next.__call__
            # this way an AttributeError while executing next() isn't hidden
            # (2.6 does this too)
        except AttributeError:
            raise TypeError("%s object is not an iterator" % type(iterator).__name__)
        try:
            return iternext()
        except StopIteration:
            if default is throw:
                raise
        return default

def project_data( repository, projects ):
    """Given a repo name, return a dict containing a list of
    historical blobs for each project.  If the "master"" commit hasn't
    changed, we can safely return the previously cached data.

    We work back from master, and we must find a non-ambiguous path.
    In each commit, we must find a blob named <project>.org.

    (Initial commit)
    v
    o-----o-----o-----o---------------------------o-----o      OK.
                       \                         /      ^
                        o-----o-----o-----o-----o       |
                                                        master

    (Initial commit)                             (parent)
    v                                              <---
    o-----o-----o-----o-------------o-------------o-----o      BAD; Ambiguous!
                       \                         /      ^
                        o-----o-----o-----o-----o       |
                                                        master


    Collect each "name"-ed project's 'org' file git.Blobs for all of
    its historical commits into:

        project["name"] = [oldest, ..., newest]

    """

    # Obtain read-only access to the Git repo 'master' branch
    repo			= git.Repo( repository )
    assert repo.bare == False
    repo.config_reader()

    master			= repo.heads.master
    commit			= master.commit

    # See if "master" commit has changed; if not, return cached result data
    if project_data.hexsha != commit.hexsha:
        # A new "master" commit; recompute result data
        project_data.hexsha	= commit.hexsha
        project_data.result	= {}

    # See what project entries remain after removing those in cache
    remains			= set( projects ) - set( project_data.result.keys() )
    if not remains:
        return project_data.hexsha, project_data.result

    # Some project data remains to be gleaned.
    while commit:
        #'''
        print "Commit %8.8s by %-20.20s on %s" % (
            commit.hexsha, commit.author, time.strftime( "%Y-%m-%d %H:%M:%S",
                                                         time.localtime( commit.committed_date )))
        print "  %s" % ( commit.message )
        for b in commit.tree.blobs:
            print "  %8.8s: %-20s: %-50.50s" % (
                b.hexsha, b.name, repr( b.data_stream.read( 50 )))
        #'''
        for p in remains:
            try:
                b		= commit.tree/(p + ".org")
            except Exception, e:
                print "No %s.org found in commit %8.8s" %( p, commit.hexsha )
                continue
            bl                  = project_data.result.setdefault( p, [] )
            if not bl or bl[-1].hexsha != b.hexsha:
                bl.insert( 0, b )
            # else:d print "Dropping duplicate blob:" + b.hexsha

        commit              = commit.parents[0] if commit.parents else None

    '''
    for p, bl in project_data.result.items():
        print "Project %s:" % ( p )
        for b in bl:
            print "  %8.8s: %-20s: %-50.50s" % (
                b.hexsha, b.name, repr( b.data_stream.read( 50 )))
    '''
    return project_data.hexsha, project_data.result

# Initial cache for project_data function
project_data.hexsha		= None
project_data.result		= None


class task( object ):
    """Represents a single task in a tree of tasks/subtasks.  Each
    task is of the form:

        .description = "Project burndown <2012-03-02 Fri>"
        .state = "TODO"
        .data = {
            "TODO": <timedict>{
                "Effort":	"22:00",
                "CLOCKSUM":	"24:00"
            },
            "DONE": <timedict>{...
        }
        .subtask = [<task>, ...]

    From this we can collect a breakdown of effort estimates and
    actual clocked time, between all the different tasks states.

    """
    def __init__( self, state, description, times=None ):
        self.state		= state
        self.description	= description
        self.data		= timedict(int)
        if times:
            for t in times:
                self.data      += t
        self.subtask		= []


    def format( self, level=1, cols=None ):
        if cols is None:
            cols                = sorted( self.data.keys(), reverse=True )
        timespecs		= collections.defaultdict(str, reversed( self.data ))

        return "| %s %-4s %-*.*s | %s |" % (
            "*" * level, self.state, 56-7-level, 56-7-level, self.description,
            " | ".join( "%8s" % timespecs[k] for k in cols ))

    def __str__( self ):
        return self._format()

    def walk( self, level=1 ):
        yield ( self, level )
        for child in self.subtask:
            for record in child.walk( level+1 ):
                yield record

    def display( self, legend=True, cols=None ):
        if cols is None:
            cols                = sorted( self.data.keys(), reverse=True )
        result			= []
        if legend:
            result.append( "| %-56s" % "Task"
                           + "".join( "| %-8s " % col
                                      for col in cols )
                           + "|" )
            result.append( "|-" + "-" * 56
                           + ( "|" + "-" * 10 ) * len( cols )
                           + "|" )
        for tsk, lvl in self.walk():
            result.append( tsk.format( lvl, cols=cols ))
        return "\n".join( result )

    def add( self, child ):
        self.subtask.append( child )

    def totals( self ):
        """
        Returns a tuple containing the following 3 data:

           o grand totals of each column, broken down by task state:
             {"TODO": <timedict>{
                 "CLOCKSUM": 10800, "Effort": 21600},
              "DONE": <timedict>{
                 "CLOCKSUM": 54800, "Effort": 50400}, ...}

           o this task's individual contribution:
             <timedict>{"CLOCKSUM": 10800, "Effort": 0}

           o all subtasks contributions:
             <timedict>{"CLOCKSUM": 75600, "Effort": 79200}
        """
        res			= {}

        # Add all the subtask's times, into per-state buckets
        for s in self.subtask:
            for state, times in s.totals()[0].iteritems():
                if state not in res:
                    res[state]	= timedict(int)
                res[state]     += times

        # We now have the totals for all children.  If our own
        # data differs from the sum of all our subtasks, they
        # must be greater -- this means that this roll-up task has
        # also accrued additional individual effort and/or
        # clocksum.  Add that, too.
        sub			= timedict(int)
        for state, times in res.items():
            sub                += times

        our			= self.data - sub
        if self.state not in res:
            res[self.state]	= timedict(int)
        res[self.state]        += our

        return res, our, sub


def parse_tasks( lines ):
    """A generator that returns a sequence of (task, level).

    At level 0, scan lines of input from the 'lines iterator
    'til we find the start of a table.  Then, deduces the column
    names, and begins to scan tasks at level 1.

    After parsing the next task, the level (number of leading *
    characters) is examined.  If it is greater than our level, the
    task is added as a sub-task.  If less or equal, the (task, leval)
    is yielded and the parse ends.
    """
    # Scan and discard lines 'til we find the beginning of an org-table
    found			= False
    for line in lines:
        if line.startswith("#+BEGIN:"):
            found		= True
            break
    if not found:
        raise Exception("No org-mode table found")

    # Parse column names, discard separator |---|
    line			= next( lines )
    cols			= list( c for c in map( string.strip, line.split( "|" )) if c )
    next( lines )

    # Parse records, yielding tasks, 'til end of org-table
    refirst			= re.compile( r"\s* \| \s* ( \*+ ) \s* ( \w+ )", re.VERBOSE )
    revalue			= re.compile( r"\s* ( [^|]* ) \|", re.VERBOSE )
    for line in lines:
        #      | ** TODO
        #        ^^ ^^^^
        pos			= 0
        match			= refirst.match( line, pos=pos )
        if match is None:
            assert line.startswith( "#+END" ), "org-mode table must end with #+END, not: '%s'" % line
            break

        stars, state		= match.groups()
        state			= state[0:4] # Limit states to 4 chars max
        pos			= match.end()

        # ...  Project burndown <2012-03-02 Fri>          |
        #      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        # ...  22:00 |
        #      ^^^^^^
        vals			= []
        while pos < len( line ):
            match		= revalue.match( line, pos=pos )
            if match is None:
                break
            vals.append( match.group(1).strip() )
            pos			= match.end()

        assert len( cols ) == len( vals )
        assert cols[0] == "Task"
        descr			= vals[0]

        yield task( state, descr, zip( cols[1:], vals[1:] )), len( stars )


def parse_task_heirarchy( lines ):
    """Produce a task heirarchy from a sequence of (task, level), correctly
    making sub-tasks a child of the correct parent task.  Return the root task.
    """
    stack		= []
    for tsk, lvl in parse_tasks( lines ):
        assert lvl > 0
        if lvl > len( stack ):
             #   | *  A  Stack Before: []
             #   | ** B  Stack After:  [A]
            assert lvl == len( stack ) + 1
            stack.append( tsk )
        elif lvl == len( stack ):
            assert lvl > 1
             #   | *  A  Stack Before: [A, B]
             #   | ** B  - Make B a child of A, replace B with new C
             # > | ** C  Stack After:  [A, C]
            stack[-2].add( stack[-1] )
            stack[-1]	= tsk
        else:
            while lvl < len( stack ):
                #   | ***
                # > | **
                stack[-2].add( stack[-1] )
                stack.pop()
    while len( stack ) > 1:
        stack[-2].add( stack[-1] )
        stack.pop()
    assert len( stack ) == 1
    return stack[0]


def project_data_parse( data, project ):
    """Return the parsed org-mode project statistics data for one
    project, from the supplied data.

    Searches each blob for an org-mode table like:

    #+BEGIN: columnview :hlines 1 :id local
    | Task                                                   | Effort | CLOCKSUM |
    |--------------------------------------------------------+--------+----------|
    | * TODO Project burndown <2012-03-02 Fri>               |  22:00 |    24:00 |
    | ** DONE Display bar chart for different periods        |   2:00 |     4:00 |
    | ** NEXT Split Bar display                              |   1:00 |     6:00 |
    | ** NEXT Horizontal/Vertical Grid Lines                 |   1:00 |          |
    | ** TODO Define JSON format                             |   2:00 |          |
    | ** TODO Create org-mode/git web service                |  16:00 |    11:00 |
    | *** DONE Load Git repo                                 |   4:00 |     4:00 |
    | *** TODO Parse org-mode data                           |   4:00 |          |
    | *** DONE Create Python web server                      |   8:00 |     7:00 |
    #+END:

    From this we harvest the aggregate data, and track the state-changes of
    tasks.  We'll create a tree of task objects from the data parsed from each
    blob, containing roll-up statistics of all of the sub-tasks in each state.

    Cache the raw task statistical data, to avoid having to reparse it.  The
    same blog may appear in many consecutive commits...

    """
    # cache[blob.hexsha] == {"todo": {}, ...} or None.
    cache			= project_data_parse.cache

    results			= {}

    results["project"]		= project
    results["list"]		= []

    # Traverse the (from oldest to newest) list, collecting the
    # differences between each.  Ignore duplicates, cache any blobs
    # parsed.
    rec, old			= None, None
    stats, prior, ahead		= None, None, None
    for blob in data[project]:
        # Now: rec, stats contains last cycle's computed data
        if blob.hexsha in cache:
            ahead		= cache[blob.hexsha] # May be None (no data found)
        else:
            try:
                print "Parsing blob %s: %s" % ( blob.hexsha, blob.name )
                ahead           = {}
                ahead["task"]	= parse_task_heirarchy(
                    iter( blob.data_stream.read().splitlines() ))
                print ahead["task"].display()
                # ... <2012-03-02 Fri> ...
                #      ^^^^^^^^^^
                match		= re.search( r"<([0-9-]*)[^>]*>",
                                             ahead["task"].description )
                if match is None:
                    raise Exception( "No date found in task: %s" % (
                        ahead["task"].description ))
                ahead["date"]	= match.group( 1 )
                ahead["date#"]	= time.mktime( time.strptime( ahead["date"],
                                                              "%Y-%m-%d" ))
                # ...Sprint 3...
                #           ^
                match		= re.search( r"[Ss]print\s+([0-9]+)",
                                             ahead["task"].description )
                sprint		= 0
                if match is not None:
                    sprint	= int( match.group( 1 ))
                ahead["sprint"]	= sprint
            except Exception, e:
                print "No Task Data: %s" % ( e )
                ahead           = None
            cache[blob.hexsha]	= ahead

        # Now: rec, stats still contains last cycle's computed data;
        # ahead contains this blob's task's data.

        # This blob may need to be ignored for various reasons.
        if ahead is None:
            print "Commit contains blob with no tasks data; skipping"
            continue
        if rec and blob.hexsha == rec["blob"]:
            print "Commit contains same blob as last; skipping"
            continue
        if stats and ahead["date"] == stats["date"]:
            # This record contains the same date as the last; must be a later
            # commit on the same day. 0 Use its data instead; throw away the
            # last record computed, and ensure we retain the same old, prior for
            # this round...
            print "Commit contains same date; re-doing %r" % ahead["date"]
            results["list"]	= results["list"][:-1]
            rec, stats		= old, prior

        # Remember this round's task's rec/stats in old/prior, to compute the
        # next round's differences.  Now safe to advance ahead to the stats just
        # loaded.
        old, prior		= rec, stats
        stats			= ahead

        # We have a valid task!  Create the summary rec for the JSON
        # result data list.
        rec			= {}
        rec["blob"]		= blob.hexsha
        rec["date"]		= stats["date"]
        rec["date#"]		= stats["date#"]
        rec["sprint"]		= stats["sprint"]

        dicts			= [		# (first contains *all* columns!)
            "total", "project",			# Overall sums
            "todo", "todoTotal",
            "done", "doneTotal",		# Done since last, and sum total
            "removed", "removedTotal",		# Existing tasks cancelled
            "added", "addedTotal",		# New tasks added/uncancelled
            "delta", "deltaTotal",		# net change and total change
        ]
        if dicts[0] not in stats:
            # We haven't yet computed the cached stats for this blob.
            #
            # total     -- all tasks.
            # todo	-- all incomplete tasks.  Tasks left to do.
            # done	-- all complete tasks.  Tasks finished.
            # project   -- all non-cancelled tasks.  Delivered
            # added	-- New Tasks added to project this period. (delta total)
            # removed   -- Tasks removed from project this period. (delta canc)
            # growth	-- Net Project added - removed this period.
            #
            stats["todoTotal"]    = timedict(int)
            stats["doneTotal"]    = timedict(int)
            stats["removedTotal"] = timedict(int)

            tot, our, sub	= stats["task"].totals()

            for k,v in tot.iteritems():
                if k in ("DONE"):
                    stats["doneTotal"] += v
                elif k in ("CANC"):
                    # Items removed from project.  Both Effort estimate
                    # (and clocked time) no longer appear in the 'total'
                    # project data, so are effectively subtracted from any
                    # others "added".
                    stats["removedTotal"] += v
                else: # ("TODO", "NEXT", "HOLD", "WAIT", "PHON", ...)
                    stats["todoTotal"] += v

            stats["project"]	= stats["todoTotal"] + stats["doneTotal"]
            stats["total"]	= stats["project"] + stats["removedTotal"]
            if prior:
                stats["todo"]	= stats["todoTotal"]    - prior["todoTotal"]
                stats["done"]	= stats["doneTotal"]    - prior["doneTotal"]
                # Added is the sum of: a) the the absolute increase in
                # the total project size (including all tasks, even
                # cancelled),
                stats["added"]		= stats["total"]        - prior["total"]
                # PLUS b) any existing tasks changed from cancelled to
                # something else; if "removed" goes -'ve, this really
                # means "added"; never let "removed" go -'ve.
                stats["removed"]= stats["removedTotal"] - prior["removedTotal"]
                for k, v in list( stats["removed"].items() ):
                    if v < 0:
                        stats["added"]   += (k, -v)
                        stats["removed"] += (k, -v)
                stats["addedTotal"] = stats["added"]	+ prior["addedTotal"]
                stats["delta"]      = stats["added"]	- stats["removed"]
                stats["deltaTotal"] = stats["delta"]	+ prior["deltaTotal"]

            # If there were no results for a stat (eg. no tasks in the
            # given state), ensure that the resultant timedict at
            # least have zero entries for all known columns.  Assumes
            # total (first item in 'dicts'') will have all columns...
            for d in dicts:
                for k in stats[dicts[0]].keys():
                    if d not in stats:
                        stats[d]  = timedict(int)
                    if k not in stats[d]:
                        stats[d] += (k, 0)

            for d in dicts:
                print task( state="",
                            description=d,
                            times=stats[d].items() ).format( level=0 )


        # Turn all the stats <timedict> back into textual time specs,
        # using their custom __reversed__ method.
        texts			= {}
        for d in dicts:
            texts[d]		= dict( reversed( stats[d] ))

        # The "estimated" (the unfortunately named Effort) column
        # deals in the total number of story points (estimated in
        # hours, roughly) for all tasks.  Map some known columns to
        # more correct names.
        mapping			= {
            "Effort":   "estimated",
            "CLOCKSUM":	"work",
        }
        for i in stats[dicts[0]].keys():
            n                   = mapping.get( i, i )
            rec[n]		= {}
            for d in dicts:
                rec[n][d]	= texts[d][i]
                rec[n][d+"#"]	= stats[d][i]

        results["list"].append( rec )
        print "Adding record %3d for %r" % ( len( results["list"] ),  rec["date"] )

    return results

project_data_parse.cache	= {}


def best_fit( points ):
    """
    Computes a best-fit line for the given [(x,y), ...] data.  Returns
    a line equation in  point, slope form x, y, slope.  Make two passes over
    the data, to avoid subtraction of nearly equal numbers.  See

        http://www.johndcook.com/blog/2008/10/20/comparing-two-ways-to-fit-a-line-to-data/
    """
    sx				= 0.0
    sy				= 0.0
    stt				= 0.0
    sts				= 0.0
    n				= len( points )
    for x, y in points:
        sx                     += x
        sy                     += y

    for x, y in points:
        t			= x - sx/n
        stt                    += t*t
        sts                    += t*y

    slope			= sts/stt           if stt else float( "inf" )
    intercept			= (sy - sx*slope)/n if n   else 0.0
    return 0.0, intercept, slope


def project_stats_transform( results, style, bestfit=True ):
    """Transform and return the project stats into the specified x-axis style.
    The incoming data is not interfered with (may be from a cache), and is
    assumed to be summary project data, in standard elapsed (calendar) time,
    possibly with gaps.  Adds a "label" field to each entry.  For the given
    style, we create equally spaced records for units of:

        elapsed	-- linear elapsed (calendar) time
        effort	-- units of clocked work on all tasks (even canceled ones)
        sprint	-- for each "Sprint #", as defined in the project name's

    These deal in processed records of the form:
        results["list"] = [{
            "work": {
                "added": 0,
                "delta": 32,
                ...
            },
            "estimated": {
                "added": 0,
                ...
            }
        }

    Lines are projected for "progress" -- amount left To Do minus the increase
    in the project scope (todoTotal - deltaTotal), "change" -- the increase in
    the project scope (deltaTotal).  Where these lines intersect is the
    projected completion of the project.

    The date[#] data is also projected, to synthesize date for any entries added
    at the end of the project.

    """
    results			= copy.copy( results )	# Shallow copy
    results["style"]		= style

    def date_components( date ):
        """YYYY-MM-DD" --> (y, m, d)"""
        y, m, d			=  ( int(x) for x in date.split( '-' ))
        return y, m, d

    filled			= []
    rec, old			= None, None
    if style == "elapsed":
        # Fill in missing days in elapsed time entries in 'filled'.
        for rec in results["list"]:
            # Take the date of 'old' (the last record in the filled list), and
            # keep copying it and advancing its date 'til we reach the date in
            # the current rec, filling in with copies of the last record's data
            # (keeping xxxxTotal data, but zeroing out the xxxxx change data)
            rec			= copy.deepcopy( rec )
            recymd		= date_components( rec["date"] )
            print "Process %s" %( repr( recymd ))
            while filled: # (Skip for very first record, otherwise loop forever)
                old		= filled[-1]
                oldymd		= date_components( old["date"] )
                if oldymd >= recymd:
                    print "old: %s" % json.dumps( old, indent=4 )
                    print "rec: %s" % json.dumps( rec, indent=4 )
                    raise Exception( "Out of order records! %r (%s) >= %r (%s)" %(
                        oldymd, old["date"], recymd, rec["date"] ))
                nxtdtm		= datetime.datetime( *oldymd ) + datetime.timedelta( 1 )
                nxtymd		= ( nxtdtm.year, nxtdtm.month, nxtdtm.day )
                if nxtymd >= recymd:
                    break

                nxt		= copy.deepcopy( old )
                nxt["date#"]	= time.mktime( nxtdtm.timetuple() )
                nxt["date"]	= nxtdtm.strftime( "%Y-%m-%d" )
                nxt["blob"]	= None
                for d in "estimated", "work":
                    for k, v in list( nxt[d].items() ):
                        if k in ( "added", "removed", "delta", "todo", "done" ):
                            nxt[d][k]		= "0:00"
                            nxt[d][k+"#"]	= 0

                print "Filling %s" % ( repr( nxtymd ))
                nxt["label"]	= "Day %d" % ( len( filled ) + 1 )
                print "  Emitting %s" % ( nxt["label"] )
                #print "  Emitting %s: %s" % (
                #    nxt["label"], json.dumps( nxt, sort_keys=True, indent=4 ))
                filled.append( nxt )
            print "Copying %s" % ( repr( recymd ))
            rec["label"]	= "Day %d" % ( len( filled ) + 1 )
            print "  Emitting %s" % ( rec["label"] )
            #print "  Emitting %s: %s" % (
            #    rec["label"], json.dumps( rec, sort_keys=True, indent=4 ))
            filled.append( rec )
    elif style == "effort":
        # Output the first record, and then scan the records, outputting a copy
        # of the latest record for each # hours of CLOCKSUM work reported.  Drop
        # the last modulo-# remainder.  By default, # == 8 hours.  When skipping
        # records due to insuffient total CLOCKSUM, sum up the 'xxxx' data from
        # intervening records, and use the last record's 'xxxxTotal' data.  Then
        # emitting the same record multiple times, zero out the 'xxxx' data for
        # the additional copies, and use the original 'xxxxTotal' data.
        increment		= day_seconds
        step			= 0
        add			= {}	# data to save from discarded records
        changekeys		= [ "added", "delta", "done", "removed", "todo" ]
        masterkeys		= [ "work", "estimated" ]
        add			= None
        for rec in results["list"]:
            # The 'rec["total"]["CLOCKSUM"]' value defines how much work has
            # been clocked, so far.  This includes all tasks, whether still in
            # 'project' or having been removed.  We uses this value (instead of
            # 'project', which doesn't include removed tasks), because we want
            # to measure progress vs. the actual amount of effort expended --
            # including tasks we later decide to remove from the project's
            # scope.
            recymd		= date_components( rec["date"] )
            print "Process %s: %5d hours effort" %( repr( recymd ),
                                                    rec["work"]["total#"]/60/60 )
            if add is None:
                add		= dict( ( d, timedict(int) ) for d in masterkeys )
            nxt			= copy.deepcopy( rec )
            if step <= nxt["work"]["total#"]:
                # We met/exceeded this step with this record.  Include any data
                # from previously dropped records, and update the corresponding
                # "xxxx" (textual "00:00" version) for each "xxxx#".  Keep
                # emiting copies 'til our step advances beyond this record's
                # total amount of work effort.  We'll use the 'add' timedict's'
                # += ( field, value ) operator to add sum to nxt.  Also, since
                # we'll be changing and re-emitting nxt, make sure we do a deep
                # copy each time, so changes don't affect the reference already
                # appended to filled...
                while step <= nxt["work"]["total#"]:
                    nxt["label"]= "Day %d" % ( len( filled ) + 1 )
                    for d in masterkeys:
                        if not add or not add[d]:
                            continue
                        for fn in add[d].keys():
                            assert fn.endswith("#")
                            print "    Adding: %-10s %-10s of %s" % (
                                d, fn, repr( dict( reversed( add[d] ))))
                            add[d]     += ( fn, nxt[d][fn] )
                        for fn, v in reversed( add[d] ):
                            f		= fn[:-1]
                            print "    Update: %-10s %-10s of %7d (%5s) to %7d (%5s)" % (
                                d, f, nxt[d][fn], nxt[d][f], add[d][fn], v )
                            nxt[d][fn]	= add[d][fn]
                            nxt[d][f]	= v

                    add		= None
                    step       += increment

                    # OK, time to add record to filled; 'nxt' record (contains
                    # any change data collected in 'add' from from discarded
                    # records.)  Zero out the xxxx change data, but retain the
                    # xxxxTotal data, in case we need to emit copies of this
                    # record.
                    print "  Emitting %s" % ( nxt["label"] )
                    #print "  Emitting %s: %s" % (
                    #    nxt["label"], json.dumps( nxt, sort_keys=True, indent=4 ))
                    filled.append( nxt )
                    nxt		= copy.deepcopy( nxt )
                    for d in masterkeys:
                      for f in changekeys:
                        fn		= f+"#"
                        nxt[d][f]       = "0:00"
                        nxt[d][fn]	= 0
            else:
                # This record doesn't include enough logged work to meet the
                # next step.  Discard it, but retain any non-zero differentials
                # to 'add' to the next record output.  Then, advance to next
                # record.  We'll simply discard this data if we never reach the
                # next 'interval' hours.
                for d in masterkeys:
                  for f in changekeys:
                    fn		= f+"#"
                    if rec[d][fn] != 0:
                        add[d] += ( fn, rec[d][fn] )
                        print "    Saving: %-10s %-10s of %7ds; now %s" % (
                            d, f, rec[d][fn], repr( dict( reversed( add[d] ))))

    else:
        raise Exception( "Unkown style: %s" % ( style ))

    results["list"]		= filled

    # Compute the burndown lines for each record.  These estimate the rate of
    # change in Effort (the estimatd time), vs. the selected X axis (which are
    # assumed to be at consistently equidistant from each-other by some
    # interpretation; "elapsed", "effort", etc.).
    #
    # The first record doesn't have one (as there is no slope), nor does any
    # empty record (one with no "list" entry).  If too much change is detected
    # (eg. too many tasks added/removed or estimates changed), then the project
    # is considered to have suffered a discontinuity, and fresh estimates will
    # be generated from that point forward.
    #
    # If 'bestfit' is not selected, we'll just use a linear average between the
    # first and current record, over (todoTotal - addedTotal) for our "progress"
    # line, and over (deltaTotal) for our "change" line.  For 'bestfit', we'll
    # compute the best fit line for the same data.  Computes the project
    # finish-x 'fx' (None if not computable)
    change_max			= 10 # Percent change indicating discontinuity
    fxmax			= None
    fxmultiple			= 3 # Allow expanding the results by this factor
    rec, zro			= None, None

    prgrdata			= []
    chngdata			= []
    datedata			= []

    num				= len( results["list"] )
    for i in xrange( num ):
        rec			= results["list"][i]
        rec["lines"]		= None

        print "Record %d, %-10s" % ( i, rec["label"] )

        # If the delta (change) in the project is greater than a certain
        # percentage of the project size, then we'll  assume a "discontinous"
        # change to the project, and compute fresh slopes.
        estdlta			= rec["estimated"]["delta#"]
        estproj			= rec["estimated"]["project#"]
        print "  Change:   %4d%%: project is %7d, change is %7d" % (
            estdlta * 100 / estproj, estproj, estdlta )
        if ( abs( estdlta ) > estproj * change_max / 100 ):
            print "    Discontinuity; %3d%% change" % ( abs( estdlta ) * 100 / estproj )
            prgrdata		= []
            chngdata		= []
            datedata		= []

        esttodoT		= rec["estimated"]["todoTotal#"]
        estdltaT		= rec["estimated"]["deltaTotal#"]
        print "  Progress: %7d (%7d todo - %7d Change)" % (
            esttodoT - estdltaT, esttodoT, estdltaT )
        prgrdata.append( (i,  esttodoT - estdltaT) )
        chngdata.append( (i, -estdltaT) )
        datedata.append( (i,  rec["date#"]) )

        if len( chngdata ) <= 1:
            # 0 or 1 data point; no line can be computed
            continue

        # We have at least 2 points!  Compute progress/change/date slopes.
        lines = rec["lines"]	= {}
        if bestfit:
            px0, py0, pslope	= best_fit( prgrdata )
            px0, py0		= int( px0 ), int( py0 )
            pC			= py0 - pslope * px0
            px1, py1		= i, int( pslope * i + pC )

            cx0, cy0, cslope	= best_fit( chngdata )
            cx0, cy0		= int( cx0 ), int( cy0 )
            cC			= cy0 - cslope * cx0
            cx1, cy1		= i, int( cslope * i + cC )

            dx0, dy0, dslope	= best_fit( datedata )
            dx0, dy0		= int( dx0 ), int( dy0 )
            dC			= dy0 - dslope * dx0
            dx1, dy1		= i, int( dslope * i + dC )
        else:
            px0, py0            = prgrdata[0]
            px1, py1            = prgrdata[i]
            pslope		= float(py0 - py1) / (px0 - px1)
            pC			= py0 - pslope * px0

            cx0, cy0		= chngdata[0]
            cx1, cy1		= chngdata[i]
            cslope		= float(cy0 - cy1) / (cx0 - cx1)
            cC			= cy0 - cslope * cx0

            dx0, dy0		= datedata[0]
            dx1, dy1		= datedata[i]
            dslope		= float(dy0 - dy1) / (dx0 - dx1)
            dC			= dy0 - dslope * dx0

        lines["progress"]	= {"x1": px0, "y1": py0, "x2": px1, "y2": py1}
        lines["change"]		= {"x1": cx0, "y1": cy0, "x2": cx1, "y2": cy1}
        lines["date"]		= {"x1": dx0, "y1": dy0, "x2": dx1, "y2": dy1}

        #
        # A line equation is:
        #
        #     y = mx + C
        #
        # To compute the intersection point of the two lines, and from that the
        # x coordinate (ordinal) where the project will be done, we need the
        # compute the C (constant) for each line equation.  We have a point and
        # a slope:
        #
        #     pslope		= (py0 - py1) / (px0 - px1)
        #
        # and from above:
        #
        #     C = y - mx
        #
        # Now, given two line equations:
        #     y = mx + b
        #     y = nx + c
        #
        # We set them equal and solve for x:
        #     mx + b = nx + c
        #     mx - nx = c - b
        #     (m-n)x = c - b
        #     x = (c - b) / (m-n)
        #
        # So we now have a formula for x. y can then be calculated using the
        # numerical value of x and one of the original formulas for y.
        #
        #     y = mx + C
        #     y = m((c - b) / (m-n)) + C
        #
        if cslope - pslope > 0:
            fx			= ( pC - cC ) / ( cslope - pslope )
            print "Slopes will intercept in future at x == %f" % ( fx )

            # Compute the number of columns required to contain the point
            # where the progress and change lines meet.  However, clamp at a
            # fxmultiple of the length of the current data...  Then, compute
            # where the progress/change lines  intercept that last bar.  This
            # will cause the lines to cross exactly at the finish point, even
            # if it is between two bars.
            fxint		= int( math.ceil( fx ))
            fxnext              = min( fxint, fxmultiple * num)
            fxmax		= max( fxnext, fxmax or 0 )

            # We can now project the finish date as of this sample
            ftimestamp		= dslope * fx + dC
            fdate		= None
            try:
                d		= datetime.date.fromtimestamp( ftimestamp )
                fdate		= "%4d-%02d-%02d (Day %d)" % (
                    d.year, d.month, d.day, fxint+1  )
                print "  Finish date at projected intercept: %s" % ( fdate )
            except:
                print "  Finish date incomputable"
                pass
            rec["finish"]	= fdate


            lines["progress"]	= {"x1":px0,    "y1":py0,
                                   "x2":fxnext, "y2":int( pslope * fxnext + pC )}
            lines["change"]     = {"x1":cx0,    "y1":cy0,
                                   "x2":fxnext, "y2":int( cslope * fxnext + cC )}
            lines["date"]       = {"x1":dx0,    "y1":dy0,
                                   "x2":fxnext, "y2":int( dslope * fxnext + dC )}
        else:
            print "Slopes will intercept in past"

        print "Record %d: progress: %-32s, %f slope (%s)" % (
            i, repr( lines["progress"] ), pslope, "best-fit" if bestfit else "linear")
        print "Record %d: change:   %-32s, %f slope" % (
            i, repr( lines["change"] ),   cslope )

        print "Record %d: date:     %-32s, %f slope" % (
            i, repr( lines["date"] ),     dslope )

    # We've computed a finish-x.  Fill in the results["list"] with empty
    # records.  Since we've computed an intersection, compute the approximate
    # projected date for each (may be impossible; if so, None)
    if fxmax is not None:
        print "Need %d total records; adding %d" % (
            fxmax + 1, fxmax - len( results["list"] ) + 1 )
    while fxmax and fxmax >= len( results["list"] ):
        rec			= copy.deepcopy( results["list"][-1] )
        rec["date#"]		= dslope * len( results["list"] ) + dC
        try:
            d			= datetime.date.fromtimestamp( rec["date#"] )
            rec["date"]		= "%4d-%02d-%02d" % ( d.year, d.month, d.day )
        except:
            rec["date"]		= None
        rec["label"]		= "Day %d" % ( len( results["list"] ) + 1 )
        rec["blob"]		= None
        rec["lines"]            = None
        rec["estimated"]        = None
        rec["work"]             = None

        print "Extend  %s" % ( rec["date"] )
        results["list"].append( rec )

    return results


def deduce_encoding( available, environ, accept=None ):
    """Deduce acceptable encoding from HTTP Accept: header:

        Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8

    If it remains None (or the supplied one is unrecognized), the
    caller should fail to produce the desired content, and return an
    HTML status code 406 Not Acceptable.

    If no Accept: encoding is supplied in the environ, the default
    (first) encoding in order is used.

    We don't test a supplied 'accept' encoding against the HTTP_ACCEPT
    settings, because certain URLs have a fixed encoding.  For
    example, /some/url/blah.json always wants to return
    "application/json", regardless of whether the browser's Accept:
    header indicates it is acceptable.  We *do* however test the
    supplied 'accept' encoding against the 'available' encodings,
    because these are the only ones known to the caller.

    Otherwise, return the first acceptable encoding in 'available'.

    """
    if accept:
        # A desired encoding; make sure it is available
        accept		= accept.lower()
        if accept not in available:
            accept	= None
        return accept

    # No predefined accept encoding; deduce preferred available one.
    # Accept: may contain */*, */json, etc.  If multiple matches,
    # select the one with the highest Accept: quality value (our
    # present None starts with a quality metric of 0.0).  Test
    # available: ["application/json", "text/html"], vs. HTTP_ACCEPT
    # "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    # Since earlier matches are the for more preferred encodings,
    # later matches must *exceed* the quality metric of the earlier.
    HTTP_ACCEPT		= environ.get( "HTTP_ACCEPT", "*/*" ).lower() if environ else "*/*"
    quality		= 0.0
    for stanza in HTTP_ACCEPT.split( ',' ):
        # application/xml;q=0.9
        q		= 1.0
        for encoding in reversed( stanza.split( ';' )):
            if encoding.startswith( "q=" ):
                q	= float( encoding[2:] )
        for avail in available:
            match	= True
            for a, t in zip( avail.split( '/' ), encoding.split( '/' )):
                if a != t and t != '*':
                    match = False
            if match:
                print "Found %16s == %-16s;q=%.1f %s %-16s;q=%.1f" % (
                    avail, encoding, q,
                    '> ' if q > quality else '<=',
                    accept, quality )
                if q > quality:
                    quality	= q
                    accept	= avail
    return accept


def http_exception( framework, status, message ):
    """Return an exception appropriate for the given web framework,
    encoding the HTTP status code and message provided.
    """
    if framework and framework.__name__ == "web":
        if status == 404:
            return framework.NotFound( message )

        if status == 406:
            class NotAcceptable( framework.NotAcceptable ):
                def __init__(self, message):
                    self.message = '; '.join( [self.message, message] )
                    framework.NotAcceptable.__init__(self)
            return NotAcceptable( message )

    elif framework and framework.__name__ == "itty":
        if status == 404:
            return framework.NotFound( message )

        if status == 406:
            class NotAcceptable( itty.RequestError ):
                status  = 406
            return NotAcceptable( message )

    return Exception( "%d %s" % ( status, message ))


#
# URL request handlers
#
#     projects_request	-- Returns all available projects, and styles
#     data_request	-- Returns statistics for one project
#

def projects_request( repository, project,
                      queries=None, environ=None, accept=None,
                      framework=None ):
    """Render a projects requests in the accepted form.  If an accept
    encoding is supplied, us it.  Otherwise, detect it from the
    environ's' "HTTP_ACCEPT"; default to "text/html".

        repository	-- A Git repository path
        project		-- A list of 1 or more project names
        queries		-- URL query options dictionary (or None)
        environ		-- HTTP request environment (headers)
        accept		-- A forced MIME encoding (eg. application/json).
        framework	-- The web framework module being used
    """
    accept		= deduce_encoding( [ "application/json",
                                             "text/javascript",
                                             "text/plain",
                                             "text/html" ],
                                           environ=environ, accept=accept )

    hexsha, data	= project_data( repository, project )
    # TODO: Deduce available graph styles from data (eg. see if sprint specified)
    styles		= [ "effort", "elapsed", "sprint" ]

    # GET Qeury options
    #     callback: function	-- wrap JSON in a function call (jsonp)
    content		= [
        {
            "project":	name,
            "styles":	styles,
        }
        for name in data.keys()
    ]

    if accept and accept in ("application/json", "text/javascript", "text/plain"):
        # JSON
        response		= ""
        callback		= queries and queries.get( 'callback', "" ) or ""
        if callback:
            response		= callback + "( "
        response               += json.dumps( content, sort_keys=True, indent=4 )
        if callback:
            response           += " )"
    elif accept in ("text/html"):
        # HTML5.  Yes, this minimal markup is cross-browser standards
        # compliant (including the unquoted attributes!)
        html			= """\
                                  <!doctype html>
                                  <meta charset=utf-8>
                                  <title>%(title)s</title>""" % {
                                      "title":	"Projects",
                                  }
        # Dump any request query options
        if queries:
            html               += """
                                  <pre>"""
            for query, value in queries.items():
                html           += """
                                    %(query)-16.16s %(value)s""" % {
                                      "query":	str( query ) + ":",
                                      "value":	value
                                  }
            html               += """
                                  </pre>"""

        # And display the available projects, and styles with URLs
        # linking to project data (adding .json to force JSON format
        # from via the browser, which doesn't normally send:
        #
        #     Accept: application/json
        #
        # in the header.
        for proj in content:
            html               += """
                                  <div class=project>%(project)s:""" % proj
            for style in proj["styles"]:
                one		= {
                    "project":	proj["project"],
                    "style":	style,
                }
                one["url"]	= cgi.escape( "/api/data/%(project)s/%(style)s.json" % one,
                                              quote=True )
                html           += """
                                    <div class=projectlink><a href="%(url)s">%(style)s</a></div>""" % one

        response	= textwrap.dedent( html )

    else:
        # Invalid encoding requested.  Return appropriate 406 Not Acceptable
        message		=  "Invalid encoding: %s, for Accept: %s" % (
            accept, environ.get( "HTTP_ACCEPT", "*.*" ))
        raise http_exception( framework, 406, message )

    # Return the content-type we've agreed to produce, and the result.
    return accept, response


def data_request( repository, project, path, style=None,
                  queries=None, environ=None, accept=None,
                  framework=None ):
    """Return the project data specified by path:

           .../<project>[/<style>][?linear]

    We'll parse the historical org-mode data, and cache it based on the
    hash of the commit.  The optional linear query option will changed
    from best-fit to linear estimation.

    The style may be provided either as an argument or as a term in
    the URL.  If none is provided, the 'effort' is assumed.
    """
    accept		= deduce_encoding( [ "application/json",
                                             "text/javascript",
                                             "text/plain",
                                             "text/html" ],
                                           environ=environ, accept=accept )

    # Confirm that project name and (optional) style are valid.
    # Default to "effort" style, because it gives us the best
    # information: based on the actual amount of effort applied, what
    # is the remaining effort that will be required to complete the
    # project.
    if style is None:
        style			= 'effort'
    proj			= None
    hexsha, data		= None, None
    try:
        terms			= path.split( "/" )
        assert 1 <= len( terms ) <= 2
        proj			= terms[0]
        if len( terms ) > 1:
            style		= terms[1]
        if style not in [ "sprint", "elapsed", "effort" ]:
            raise Exception( "Unknown style for project '%s': %s" % ( proj, style ))

        try:
            hexsha, data	= project_data( repository, [ proj ] )
        except Exception, e:
            raise http_exception( framework, 500,
                                  "Project data bad: %s" % ( e.message ))
        if proj not in data.keys():
            raise http_exception( framework, 404, "Unknown project: %s" % ( proj ))
    except Exception, e:
        # Invalid project/style requested.  Return 404 Not Found
        raise http_exception( framework, 404, e.message )

    # hexsha updated, data[proj] available.  Check that our cached data still
    # valid and/or exists, and go parse it if not.
    if data_request.hexsha != hexsha:
        data_request_hexsha	= hexsha
        data_request.cache	= {}
    stats			= data_request.cache.get( proj, None )
    if not stats:
        stats                   = project_data_parse( data, proj )
        data_request.cache[proj]= stats


    # Transform the raw stats into the desired x-axis style.  We must perform a
    # shallow copy of the stats dict, because we modify it "in-place".  We
    # promise to do a deep copy of any dicts within this that we have to change.
    trans			= project_stats_transform(
        stats, style, bestfit=(( "linear" not in queries ) if queries else True ))

    response			= None
    if accept and accept in ("application/json", "text/javascript", "text/plain"):
        response		= ""
        callback		= queries and queries.get( 'callback', "" ) or ""
        if callback:
            response		= callback + "( "
        response               += json.dumps( trans, sort_keys=True, indent=4 )
        if callback:
            response           += " )"

    #elif accept and accept in ("text/html"):
    #    pass

    else:
        # Invalid encoding requested.  Return appropriate 406 Not Acceptable
        message			=  "Invalid encoding: %s, for Accept: %s" % (
            accept, environ.get( "HTTP_ACCEPT", "*.*" ))
        raise http_exception( framework, 406, message )

    return accept, response

# Initial cache for data_request function
data_request.hexsha		= None
data_request.cache		= None

#
# Web Server
#
#     When executed as an command, invokes a webserver (by default
# using web.py, running on all interfaces, port 8080).  Responds to
# Accept: application/json in the HTTP request header, and also to
# appending ".json" to any URL, to force JSON response.  Run:
#
#     orgserver.py ~/org project another
#
# to parse and serve org-mode data from the Git repository there, for
# projects "project" and "another".
#
if __name__ == "__main__":

    # Parse args
    parser			= argparse.ArgumentParser(
        description = "Provide HTTP access to project data in the supplied Git repository",
        epilog = "" )

    parser.add_argument( '-s', '--server',
                         default="web.py",
                         help="Webserver framework to use (web.py, itty)" )
    parser.add_argument( '-a', '--address',
                         default="0.0.0.0:8080",
                         help="Default interface[:port] to bind to (default: all, port 80)" )
    parser.add_argument( '-l', '--log',
                         help="Log file, if desired" )
    parser.add_argument( '-d', '--day',
                         help="Hours in a day (default: %d)" % day_seconds )
    parser.add_argument( '-r', '--redundant', action="store_true",
                         help="If server is already bound to port, fail quietly" )
    parser.add_argument( '--style',
                         default=None,
                         help="Specify a default style; if None, default is 'effort'" )
    parser.add_argument( 'repository', nargs=1 )
    parser.add_argument( 'project', nargs="+" )
    args			= parser.parse_args()

    # Deduce interface:port to bind, and correct types
    address			= args.address.split(':')
    assert 1 <= len( address ) <= 2
    address			= ( str( address[0] ),
                                    int( address[1] ) if len( address ) > 1 else 80 )
    if args.day:
        day_seconds		= int( float( args.day ) * 60 *60 )

    # Implement the various Web Servers
    if args.server == "web.py":
        """The web.py webserver.

        When invoked using:

            curl -vo - --header "Accept: application/json" http://localhost:8080/api/projects.json?foo=bar

        the web.ctx available to the handler class' GET method looks
        like this:

        <ThreadedDict {
            'status':           '200 OK',
            'realhome':         u'http://localhost:8080',
            'homedomain':	u'http://localhost:8080',
            'protocol':         u'http',
            'app_stack':        [<web.application.application instance at 0x1015806c8>],
            'ip':               u'127.0.0.1',
            'fullpath':         u'/api/projects.json?foo=bar',
            'headers':          [],
            'host':             u'localhost:8080',
            # ('env' duplicates 'environ' dict)
            'environ': {
                'ACTUAL_SERVER_PROTOCOL': 'HTTP/1.1',
                'HTTP_ACCEPT':          'application/json',
                'HTTP_HOST':            'localhost:8080',
                'HTTP_USER_AGENT':      'curl/7.21.4 (universal-apple-darwin11.0) libcurl/7.21.4 OpenSSL/0.9.8r zlib/1.2.5',
                'PATH_INFO':            '/api/projects.json',
                'QUERY_STRING':         'foo=bar',
                'REMOTE_ADDR':		'127.0.0.1',
                'REMOTE_PORT':          '64328',
                'REQUEST_METHOD':       'GET',
                'REQUEST_URI':          '/api/projects.json?foo=bar',
                'SCRIPT_NAME':          '',
                'SERVER_NAME':          'localhost',
                'SERVER_PORT':          '8080',
                'SERVER_PROTOCOL':      'HTTP/1.1',
                'SERVER_SOFTWARE':	'CherryPy/3.2.0 Server',
                'wsgi.errors':		<open file '<stderr>', mode 'w' at 0x100f0a270>,
                'wsgi.input':           <web.wsgiserver.KnownLengthRFile object at 0x1015f4450>,
                'wsgi.multiprocess':    False
                'wsgi.multithread':     True,
                'wsgi.run_once':	False,
                'wsgi.url_scheme':       'http',
                'wsgi.version':		(1, 0),
            },
            'home':		u'http://localhost:8080',
            'homepath':         u'',
            'output':           u'',
            'path':		u'/api/projects.json',
            'query':            u'?foo=bar',
            'method':		u'GET'
        }>

        When invoked from Chrome, it is very similar (differences outlined below):

        <ThreadedDict {
            ...
            'environ': {
                ...
                'HTTP_ACCEPT': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'HTTP_ACCEPT_CHARSET': 'ISO-8859-1,utf-8;q=0.7,*;q=0.3',
                'HTTP_ACCEPT_ENCODING': 'gzip,deflate,sdch'
                'HTTP_ACCEPT_LANGUAGE': 'en-US,en;q=0.8',
                'HTTP_CACHE_CONTROL': 'max-age=0',
                'HTTP_CONNECTION': 'keep-alive',
                'HTTP_USER_AGENT': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_7_3) AppleWebKit/535.11 (KHTML, like Gecko) Chrome/17.0.963.56 Safari/535.11',
                ...
            },
            ...
        }>

        """

        import web
        import wsgilog

        urls			= (
            "/(.*)/",				"trailing_slash",
            "/",				"home",
            "/api/projects(.json)?",		"projects",
            "/api/data/(.*)",			"data",
            "/(.*)",				"html",
        )

        class trailing_slash:
            def GET( self, path ):
                web.seeother( '/' + path )

        class home:
            def GET( self ):
                """Forward to an appropriate start page.  Detect if behind a
                proxy, and use the original forwarded host.
                """
                # print json.dumps(web.ctx, skipkeys=True, default=repr, indent=4,)
                proxy		= web.ctx.environ.get( "HTTP_X_FORWARDED_HOST", "" )
                if proxy:
                    proxy	= "http://" + proxy
                target		= proxy + "/static/burn-down-charts.html"
                print "Redirect / to %s" % ( target )
                web.seeother( target )

        class projects:
            def GET( self, path ):
                """Deduce accept encoding from Accept: header, or
                force JSON if .json path was explicitly requested.
                """
                environ		= web.ctx.environ
                queries		= web.input()
                accept		= None
                if path and path.endswith( ".json" ):
                    accept	= "application/json"

                # Always returns a content-type and response.  If an
                # exception is raised, it should be an appropriate one
                # from the supplied framework to carry a meaningful
                # HTTP status code.  Otherwise, a generic 500 Server
                # Error will be produced.
                content, response = projects_request( args.repository[0], args.project,
                                                      queries=queries, environ=environ,
                                                      accept=accept, framework=web )
                web.header( "Cache-Control", "no-cache" )
                web.header( "Content-Type", content )
                return response

        class data:
            def GET( self, path ):
                environ		= web.ctx.environ
                queries		= web.input()
                accept		= None
                if path.endswith( ".json" ):
                    accept	= "application/json"
                    path	= path[:-5] # Clip off .json

                content, response = data_request( args.repository[0], args.project, path,
                                                  style=args.style,
                                                  queries=queries, environ=environ,
                                                  accept=accept, framework=web )
                web.header( "Cache-Control", "no-cache" )
                web.header( "Content-Type", content )
                return response

        class html:
            """
            If an arbitary /*[.html] name is provided, look for a
            corresponding .html file in the org directory.
            """
            def GET( self, path ):
                environ		= web.ctx.environ
                queries		= web.input()
                accept		= None
                if path.endswith( ".html" ):
                    path	= path[:-5] # Clip off .html; it is assumed
                try:
                    final	= os.path.join( args.repository[0], path + ".html" )
                    with open( final, 'r' ) as f:
                        response = f.read()
                except Exception, e:
                    raise http_exception( web, 404, e.message )

                web.header( "Content-Type", "text/html" )
                return response
                    

        # web.py sets up logging, and then prints (hence logs) the address it is
        # about to bind to before it actually binds.  Thus, if another server is
        # already running on the port, the bind fails -- but output has already
        # been logged to the same log file destination, disturbing the log file.
        #
        class Log( wsgilog.WsgiLog ):
            def __init__( self, application ):
                """Set up logging, and then make sure sys.stderr goes to
                whereever sys.stdout is now going.  This ensures that
                environ['wsgi.errors'] (which is always set to sys.stderr by
                web.py) goes to the log file; this is used to log each incoming
                HTTP request.  After setup, disable logging, 'til our first WSGI
                call is made (the bind to server address has succeeded).

                We want to be able to try to bind, and fail -- producing only
                sys.stdout/stderr output.  When the first WSGI call is made,
                then enable logging.  This may let a few log messages through,
                because we don't handle /static/... URLs, so our wsgi middleware
                doesn't get called 'til we handle something....
                """
                wsgilog.WsgiLog.__init__(
                    self, application,
                    logformat="%(message)s",
                    log		= True,
                    tohtml	= True,			# Exceptions generate HTML
                    tofile	= True,			# Send logging to file
                    file	= args.log,		# Target log file (may be None!)
                    interval	= 'h',
                    backups	= 1,
                )
                self.log	= False

            def __call__( self, *args ):
                if not self.log:
                    self.log	= True
                    sys.stdout	= wsgilog.LogStdout( self.logger, logging.DEBUG )
                    sys.stderr	= sys.stdout
                return wsgilog.WsgiLog.__call__( self, *args )

        try:
            # Get the required web.py classes from the local namespace.  The
            # iface:port must always passed on argv[1] to use app.run(), so use
            # lower-level web.httpserver.runsimple interface.  This sets up the
            # WSGI middleware chain, prints the address and then invokes
            # httpserver.WSGIserver.start(), which does the bind, and then makes
            # WSGI calls (enabling our logging, above).
            middleware		= []
            if args.log:
                middleware.append( Log )
            app			= web.application( urls, locals() )
            web.httpserver.runsimple( app.wsgifunc( *middleware ), address )
        except socket.error:
            if not args.redundant:
                # Ignore errors binding on socket; drop through and exit cleanly.
                raise

    elif args.server == "itty":
        """The itty webserver is a very small Python native webserver
        that recognizes the "Accept: " header, and allows different
        implementations to be triggered for each "Accept: ...""
        type, for each HTTP URL.

        The request passed to each index function contains the Query
        options, available as a dict from request.<method>
        (eg. request.GET), and the request environment is available in
        request._environ (contains *both* the shell environment, as
        well as the HTTP request environment):
        {
            'Apple_PubSub_Socket_Render': '/tmp/launch-OVKXKk/Render',
            'Apple_Ubiquity_Message':   '/tmp/launch-dbg33P/Apple_Ubiquity_Message',
            'COLUMNS':                  '112',
            'COMMAND_MODE':		'unix2003',
            'COM_GOOGLE_CHROME_FRAMEWORK_SERVICE_PROCESS/USERS/LAVERNE/LIBRARY/APPLICATION_SUPPORT/GOOGLE/CHROME_SOCKET': '/tmp/launch-6Lanzn/ServiceProcessSocket',
            'CONTENT_LENGTH':		'',
            'CONTENT_TYPE':		'text/plain',
            'DISPLAY':			'/tmp/launch-5qBefz/org.x:0',
            'EMACS':                    't',
            'GATEWAY_INTERFACE':        'CGI/1.1',
            'GIT_PS1_SHOWDIRTYSTATE':   '1',
            'GIT_PS1_SHOWUNTRACKEDFILES': '1',
            'GIT_PS1_SHOWUPSTREAM':     'auto',
            'HOME':                     '/Users/laverne',
            'HTTP_ACCEPT':              'application/json',
            'HTTP_HOST':                'localhost:8080',
            'HTTP_USER_AGENT':          'curl/7.21.4 (universal-apple-darwin11.0) libcurl/7.21.4 OpenSSL/0.9.8r zlib/1.2.5',
            'INSIDE_EMACS':             't',
            'ITERM_PROFILE':            'Default',
            'ITERM_SESSION_ID':         'w0t0p0',
            'LANG':                     'en_CA.UTF-8',
            'LOGNAME':                  'laverne',
            'PATH':                     '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/usr/X11/bin:/usr/local/git/bin',
            'PATH_INFO':                '/api/projects.json',
            'PWD':                      '/Users/laverne/src/burndown/bin',
            'PYTHONPATH':               '/usr/local/lib/python2.7/site-packages',
            'QUERY_STRING':             'foo=bar',
            'REMOTE_ADDR':              '127.0.0.1',
            'REMOTE_HOST':              '1.0.0.127.in-addr.arpa',
            'REQUEST_METHOD':           'GET',
            'SCRIPT_NAME':              '',
            'SERVER_NAME':              '1.0.0.127.in-addr.arpa',
            'SERVER_PORT':              '8080',
            'SERVER_PROTOCOL':          'HTTP/1.1',
            'SERVER_SOFTWARE':          'WSGIServer/0.1 Python/2.7.1',
            'SHELL':                    '/bin/bash',
            'SHLVL':                    '2',
            'SSH_AUTH_SOCK':            '/tmp/launch-anCnoA/Listeners',
            'STY':                      '39661.ttys000.macpro',
            'TERM':			'dumb',
            'TERMCAP':                  '',
            'TERM_PROGRAM':             'iTerm.app',
            'TMPDIR':                   '/var/folders/vm/sbw2f_2d3ps_rx7y85pf8rwc0000gn/T/',
            'USER':                     'laverne',
            'VERSIONER_PYTHON_PREFER_32_BIT': 'no',
            'VERSIONER_PYTHON_VERSION': '2.7',
            'WINDOW':                   '0',
            '_':                        '/Users/laverne/src/burndown/bin/org-server.py',
            '__CF_USER_TEXT_ENCODING':	'0x1F5:0:0',
            'wsgi.errors':              <open file '<stderr>', mode 'w' at 0x105f2f270>,
            'wsgi.file_wrapper': <class wsgiref.util.FileWrapper at 0x1064ba050>,
            'wsgi.input': <socket._fileobject object at 0x106488bd0>,
            'wsgi.multiprocess':        False,
            'wsgi.multithread': True,
            'wsgi.run_once': False,
            'wsgi.url_scheme': 'http',
            'wsgi.version': (1, 0),
        }
        """
        import itty

        #     Instead of just returning the response directly and
        # taking the default headers, encode the supplied
        # Content-Type.
        @itty.get( "/" )
        def index( request ):
            raise itty.Redirect( "api/projects" )

        @itty.get( "/api/projects" )
        def index( request ):
            """
            Responds according to content of Accept: header.
            """
            queries		= request.GET
            environ		= request._environ
            content, response	= projects_request( args.repository[0], args.project,
                                                    queries=queries, environ=environ,
                                                    framework=itty )
            return itty.Response( response, headers=[
                ("Cache-Control", "no-cache"),
                ("Content-Type", content)
            ])

        @itty.get( "/api/projects.json" )
        def index( request ):
            """
            Explcit .json; forces JSON, regardless of Accept: header.
            """
            queries		= request.GET
            environ		= request._environ
            content, response	= projects_request( args.repository[0], args.project,
                                                    queries=queries, environ=environ,
                                                    accept="application/json", framework=itty )
            return itty.Response( response, headers=[
                ("Cache-Control", "no-cache"),
                ("Content-Type", content)
            ])

        itty.run_itty( host=address[0], port=address[1] )

    else:
        raise Exception("Unknown Web Server framework: %s" % ( args.server ))
