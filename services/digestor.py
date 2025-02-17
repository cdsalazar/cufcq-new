import csv
import logging
import os
import rethinkdb as r
from time import sleep
from models.fcq import Fcq
from models.instructor import Instructor
from models.course import Course
from models.department import Department
from os import listdir
from os.path import isfile, join


class dataSet:
    """
    Class to store a digested csv
    """
    def __init__(self, location):
        with open(location, "r") as myfile:
            self.headers = self.toArray(myfile.readline())
            self.stringData = myfile.readlines()
            self.raw_data = self.transformData(self.stringData, self.headers)

    def toArray(self, string):
        reader = csv.reader([string], delimiter=',')
        array = []
        for read in reader:
            for a in read:
                array.append(a.strip())
        return array

    def transformData(self, data, headers):
        """
        Input Parameters:
            data: The data that is read from the file. list of strings
            attribute: The attribute you want to consider from the file

        Returns a list of floats parsed from the ithAttribute of the list of strings
        """
        dataArray = []
        for i, string in enumerate(data):
            dataDict = {}
            for j, dataValue in enumerate(self.toArray(string)):
                # print("{0}\t\t{1}".format(headers[j], dataValue))
                dataDict[headers[j]] = dataValue
            dataArray.append(dataDict)
        return dataArray

    def write(self, location, data):
        with open(location, "w") as outfile:
            outfile.write(str(data)[1:-1])


def digest(filename, db, conn):
    if filename == 'ALL':
        allfiles = [f for f in listdir('data/csv/') if isfile(join('data/csv/', f))]
        for f in allfiles:
            logging.info("{0} ".format(f))
            digest_file(f, db, conn)
        return
    else:
        digest_file(filename, db, conn)


def digest_file(filename, db, conn):
    data = dataSet('data/csv/' + filename)
    fcq_data = list(map(Fcq().sanitize_from_raw, data.raw_data))
    for model in [Department(), Course(), Instructor()]:
        sanitized_data = list(map(model.sanitize_from_raw, fcq_data))
        sanitized_data = list({v['id']: v for v in sanitized_data}.values())
        modeltable = model.__class__.__name__
        result = r.db(db).table(modeltable).insert(sanitized_data).run(conn)
        inserted = result['inserted']
        errors = result['errors']
        logging.info("{0} Inserted \t {1} \t Skipped {2}".format(modeltable.ljust(12), inserted, errors))
    result = r.db(db).table('Fcq').insert(fcq_data).run(conn)
    inserted = result['inserted']
    errors = result['errors']
    logging.info("{0} \t Inserted {1}".format(filename.ljust(12), inserted))
    if errors:
        first_error = result['first_error']
        logging.warn("{0} Errors inserting fcqs. First Error:\n{1}".format(errors, first_error))


def cleanup(db, conn):
    associate(db, conn)
    overtime(db, conn)
    # stats_data(db, conn)
    # grade_data(db, conn)


def has_many(db, conn, model, has_many, has_many_id=None):
    model_id = "{0}_id".format(model).lower()
    has_many_plural = "{0}s".format(has_many).lower()
    if not has_many_id:
        has_many_id = "{0}_id".format(has_many).lower()
    grouped_model = r.db(db).table('Fcq').group(model_id).get_field(has_many_id).ungroup().for_each(
        lambda doc: r.db(db).table(model).get(doc['group']).update({has_many_plural: doc['reduction'].distinct()})
    ).run(conn, array_limit=200000)
    logging.info(grouped_model)


def overtime(db, conn):
    try:
        model_overtime(db, conn)
    except r.errors.ReqlQueryLogicError as err:
        logging.error('Overtime query failed. This can be because a model does not have any fcqs \
        associated with it. Ensure there are no documents in the tables that have empty \'fcqs\' \
        fields. Here comes the real error:')
        raise err


def model_overtime(db, conn):
    def _general_overtime(doc, val):
        return {
            'total_fcqs': val['reduction'].count(),
            'total_forms_requested': val['reduction'].sum('forms_requested'),
            'total_forms_returned': val['reduction'].sum('forms_returned'),
            'denver_data_averages': r.branch(((doc.get_field('campus').default(None) == 'DN') & (val['group'] <= 20144)), {
                'r_fairness': val['reduction'].get_field('denver_data').get_field('r_fairness').avg().default(None),
                'r_presentation': val['reduction'].get_field('denver_data').get_field('r_presentation').avg().default(None),
                'r_workload': val['reduction'].get_field('denver_data').get_field('r_workload').avg().default(None),
                'r_diversity': val['reduction'].get_field('denver_data').get_field('r_diversity').avg().default(None),
                'r_accessibility': val['reduction'].get_field('denver_data').get_field('r_accessibility').avg().default(None),
                'r_learning': val['reduction'].get_field('denver_data').get_field('r_learning').avg().default(None),
            }, None)
        }

    def _general_stats(doc):
        return {
            'total_fcqs': doc['fcq_data'].count(),
            'total_forms_requested': doc['fcq_data'].sum('forms_requested'),
            'total_forms_returned': doc['fcq_data'].sum('forms_returned'),
        }

    def _instructor_overtime(doc, val, unchained=False):
        iot = {
            'GR_fcqs': val['reduction'].filter({'level': 'GR'}).count(),
            'UD_fcqs': val['reduction'].filter({'level': 'UD'}).count(),
            'LD_fcqs': val['reduction'].filter({'level': 'LD'}).count(),
            'total_courses': val['reduction'].get_field('course_id').distinct().count(),
            'instructoroverall_average': val['reduction'].get_field('instructoroverall').avg().default(None),
            'instructoroverall_sd_average': val['reduction'].get_field('instructoroverall_sd').avg().default(None),
            'instructor_effectiveness_average': val['reduction'].get_field('instructor_effectiveness').avg().default(None),
            'instructor_availability_average': val['reduction'].get_field('instructor_availability').avg().default(None),
            'instructor_respect_average': val['reduction'].get_field('instructor_respect').avg().default(None)
        }
        chain = {} if unchained else _general_overtime(doc, val)
        iot.update(chain)
        return iot

    def _instructor_stats(doc, unchained=False):
        iot = {
            'GR_fcqs': doc['fcq_data'].filter({'level': 'GR'}).count(),
            'UD_fcqs': doc['fcq_data'].filter({'level': 'UD'}).count(),
            'LD_fcqs': doc['fcq_data'].filter({'level': 'LD'}).count(),
            'total_courses': doc['fcq_data'].get_field('course_id').distinct().count(),
            'instructoroverall_average': doc['fcq_data'].get_field('instructoroverall').avg().default(None),
            'instructoroverall_sd_average': doc['fcq_data'].get_field('instructoroverall_sd').avg().default(None),
            'instructor_effectiveness_average': doc['fcq_data'].get_field('instructor_effectiveness').avg().default(None),
            'instructor_availability_average': doc['fcq_data'].get_field('instructor_availability').avg().default(None),
            'instructor_respect_average': doc['fcq_data'].get_field('instructor_respect').avg().default(None)
        }
        chain = {} if unchained else _general_stats(doc)
        iot.update(chain)
        return iot

    def _course_overtime(doc, val, unchained=False):
        cot = {
            'total_instructors': val['reduction'].get_field('instructor_id').distinct().count(),
            'courseoverall_average': val['reduction'].get_field('courseoverall').avg().default(None),
            'courseoverall_sd_average': val['reduction'].get_field('courseoverall_sd').avg().default(None),
            'course_challenge_average': val['reduction'].get_field('course_challenge').avg().default(None),
            'course_howmuchlearned_average': val['reduction'].get_field('course_howmuchlearned').avg().default(None),
            'course_priorinterest_average': val['reduction'].get_field('course_priorinterest').avg().default(None)
        }
        chain = {} if unchained else _general_overtime(doc, val)
        cot.update(chain)
        return cot

    def _course_stats(doc, unchained=False):
        cot = {
            'total_instructors': doc['fcq_data'].get_field('instructor_id').distinct().count(),
            'courseoverall_average': doc['fcq_data'].get_field('courseoverall').avg().default(None),
            'courseoverall_sd_average': doc['fcq_data'].get_field('courseoverall_sd').avg().default(None),
            'course_challenge_average': doc['fcq_data'].get_field('course_challenge').avg().default(None),
            'course_howmuchlearned_average': doc['fcq_data'].get_field('course_howmuchlearned').avg().default(None),
            'course_priorinterest_average': doc['fcq_data'].get_field('course_priorinterest').avg().default(None)
        }
        chain = {} if unchained else _general_stats(doc)
        cot.update(chain)
        return cot

    def _department_overtime(doc, val):
        iot = _instructor_overtime(doc, val, unchained=True)
        cot = _course_overtime(doc, val, unchained=True)
        got = _general_overtime(doc, val)
        dot = {
            'GR_courses': val['reduction'].filter({'level': 'GR'}).get_field('course_id').distinct().count(),
            'UD_courses': val['reduction'].filter({'level': 'UD'}).get_field('course_id').distinct().count(),
            'LD_courses': val['reduction'].filter({'level': 'LD'}).get_field('course_id').distinct().count(),
            'TA_instructors': val['reduction'].filter({'instructor_group': 'TA'}).get_field('instructor_id').distinct().count(),
            'OTH_instructors': val['reduction'].filter({'instructor_group': 'OTH'}).get_field('instructor_id').distinct().count(),
            'TTT_instructors': val['reduction'].filter({'instructor_group': 'TTT'}).get_field('instructor_id').distinct().count(),
            'TA_instructoroverall_average': val['reduction'].filter({'instructor_group': 'TA'}).get_field('instructoroverall').avg().default(None),
            'OTH_instructoroverall_average': val['reduction'].filter({'instructor_group': 'OTH'}).get_field('instructoroverall').avg().default(None),
            'TTT_instructoroverall_average': val['reduction'].filter({'instructor_group': 'TTT'}).get_field('instructoroverall').avg().default(None),
            'GR_courseoverall_average': val['reduction'].filter({'level': 'GR'}).get_field('courseoverall').avg().default(None),
            'UD_courseoverall_average': val['reduction'].filter({'level': 'UD'}).get_field('courseoverall').avg().default(None),
            'LD_courseoverall_average': val['reduction'].filter({'level': 'LD'}).get_field('courseoverall').avg().default(None),
            'GR_forms_requested': val['reduction'].filter({'level': 'GR'}).sum('forms_requested'),
            'UD_forms_requested': val['reduction'].filter({'level': 'UD'}).sum('forms_requested'),
            'LD_forms_requested': val['reduction'].filter({'level': 'LD'}).sum('forms_requested')
        }
        dot.update(iot)
        dot.update(cot)
        dot.update(got)
        return dot

    def _department_stats(doc):
        iot = _instructor_stats(doc, unchained=True)
        cot = _course_stats(doc, unchained=True)
        got = _general_stats(doc)
        dot = {
            'GR_courses': doc['fcq_data'].filter({'level': 'GR'}).get_field('course_id').distinct().count(),
            'UD_courses': doc['fcq_data'].filter({'level': 'UD'}).get_field('course_id').distinct().count(),
            'LD_courses': doc['fcq_data'].filter({'level': 'LD'}).get_field('course_id').distinct().count(),
            'TA_instructors': doc['fcq_data'].filter({'instructor_group': 'TA'}).get_field('instructor_id').distinct().count(),
            'OTH_instructors': doc['fcq_data'].filter({'instructor_group': 'OTH'}).get_field('instructor_id').distinct().count(),
            'TTT_instructors': doc['fcq_data'].filter({'instructor_group': 'TTT'}).get_field('instructor_id').distinct().count()
        }
        dot.update(iot)
        dot.update(cot)
        dot.update(got)
        return dot

    # model_overtime
    for model in ['Instructor', 'Department', 'Course']:
        _model_overtime = {
            'Instructor': _instructor_overtime,
            'Department': _department_overtime,
            'Course': _course_overtime
        }[model]
        _model_stats = {
            'Instructor': _instructor_stats,
            'Department': _department_stats,
            'Course': _course_stats
        }[model]
        overtime_query = r.db(db).table(model).merge(
            lambda doc: {'fcq_data': r.db(db).table('Fcq').get_all(r.args(doc['fcqs'])).coerce_to('array')}
        ).for_each(
            lambda doc: r.db(db).table(model).get(doc['id']).update({'overtime': doc['fcq_data'].group('yearterm').ungroup().map(
                lambda val: [val['group'].coerce_to('string'), _model_overtime(doc, val)]
            ).coerce_to('object'), 'stats': _model_stats(doc)})
        ).run(conn, array_limit=200000)
        logging.info(overtime_query)

# Mode:
# r.expr([1,2,2,2,3,3]).group(r.row).count().ungroup().orderBy('reduction').nth(-1)('group')`


def associate(db, conn):
    has_many(db, conn, 'Course', 'Fcq', has_many_id='id')
    has_many(db, conn, 'Course', 'yearterm', has_many_id='yearterm')
    has_many(db, conn, 'Course', 'alternate_title', has_many_id='course_title')
    has_many(db, conn, 'Course', 'Instructor')
    has_many(db, conn, 'Instructor', 'Fcq', has_many_id='id')
    has_many(db, conn, 'Instructor', 'yearterm', has_many_id='yearterm')
    has_many(db, conn, 'Instructor', 'Course')
    has_many(db, conn, 'Department', 'Fcq', has_many_id='id')
    has_many(db, conn, 'Department', 'yearterm', has_many_id='yearterm')
    has_many(db, conn, 'Department', 'Instructor')
    has_many(db, conn, 'Department', 'Course')
