from flask import render_template, flash, redirect, url_for, request, jsonify, \
    current_app, abort, send_from_directory
from flask_babelex import lazy_gettext as _
from flask_login import current_user, login_required, login_user, logout_user

from rapidannotator import db
from rapidannotator import bcrypt
from rapidannotator.models import User, Experiment, AnnotatorAssociation, File, \
    AnnotationInfo, AnnotationLevel, Label
from rapidannotator.modules.annotate_experiment import blueprint
from .api import isAnnotator

from sqlalchemy import and_
import json

@blueprint.before_request
def before_request():
    if current_app.login_manager._login_disabled:
        pass
    elif not current_user.is_authenticated:
        return "Please login to access this page."

@blueprint.route('/a/<int:experimentId>')
@isAnnotator
def index(experimentId):

    experiment = Experiment.query.filter_by(id=experimentId).first()
    annotatorInfo = AnnotatorAssociation.query.filter_by(user_id=current_user.id).\
                    filter_by(experiment_id=experimentId).first()
    keyBindingDict = makeKeyBindingDict(experimentId)
    currentFileIndex = annotatorInfo.current
    firstFile = annotatorInfo.start
    lastFile = annotatorInfo.end

    if lastFile == -1:
        lastFile = experiment.files.count()

    ''' It is to make compatible with 0-based indexing '''
    lastFile = lastFile - firstFile - 1

    if currentFileIndex <= lastFile:
        currentFile = _getFile(experimentId, currentFileIndex, firstFile)
    else:
        currentFile = []

    is_done = int(experiment.is_done)
    ''' TODO! move current back to original value if any file was deleted '''

    ''' For displaying a warning if the labels got changed at any time'''
        
    labelCount = 0
    labelWarning = 0

    annotationLevels = AnnotationLevel.query.filter_by(experiment_id=experimentId).all()
    for level in annotationLevels:
        labels = Label.query.filter_by(annotation_id=level.id)
        labelCount += labels.count()
 
    if experiment.countLabel != labelCount and experiment.countLabel != -1 and labelCount != 0:
        if currentFileIndex > 0:
            labelWarning = 1
    
    if labelCount == 0:
        experiment.countLabel = -1
        db.session.commit()
    else:
        experiment.countLabel = labelCount
        db.session.commit()

    if lastFile == -1:
        progress_width = 0
    else:
        progress_width = round((currentFileIndex/ (lastFile  + 1))*100, 2)

    isExpowner =  int((current_user in  experiment.owners))

    return render_template('annotate_experiment/main.html',
        experiment = experiment,
        currentFile = currentFile,
        currentFileIndex = currentFileIndex,
        lastFile = lastFile,
        firstFile = firstFile,
        keyBindingDict = keyBindingDict,
        is_done = is_done,
        labelCount = labelCount,
        labelWarning = labelWarning,
        progress_width = progress_width,
        isExpowner = isExpowner
    ) 

def makeKeyBindingDict(experimentId):
    levels = AnnotationLevel.query.filter_by(experiment_id=\
                experimentId).order_by(AnnotationLevel.level_number)
    index, keyBindingDict = 1, {}

    for level in levels:
        labels = Label.query.filter_by(annotation_id=level.id).all()
        keySet, labelDict = [], {}

        for label in labels:
            if label.key_binding:
                keySet.append(label.key_binding)

        for label in labels:
            if not label.key_binding:
                defaultKey = getDefaultKey(keySet)
                keySet.append(defaultKey)

            key = label.key_binding if label.key_binding else defaultKey
            labelDict[label.id] = key

        keyBindingDict[index] = labelDict
        index += 1

    return keyBindingDict

def getDefaultKey(keySet):
    for i in range(26):
        k = chr(i + 97)
        if k not in keySet:
            return k
    return ''

'''
    .. params:
        experimentId: id of the experiment
        fileIndex: index of the file to fetch
'''
def _getFile(experimentId, fileIndex, start):
    experiment = Experiment.query.filter_by(id=experimentId).first()
    currentFile = experiment.files.order_by(File.id)[fileIndex + start]    

    currentFile = {
        'id' : currentFile.id,
        'name' : currentFile.name,
        'content' : currentFile.content,
        'caption' : currentFile.caption,
    }
    return currentFile

'''
    TODO? NOT USED YET
    .. updates the value of current to the value given in params
'''
def _updateCurrentFileIndex(experimentId, currentFileIndex):
    annotatorInfo = AnnotatorAssociation.query.filter(and_\
                        (AnnotatorAssociation.user_id==current_user.id, \
                        AnnotatorAssociation.experiment_id==experimentId)).first()

    annotatorInfo.current = currentFileIndex
    db.session.commit()
    response = {}
    response['success'] = True

    return jsonify(response)

'''
    wrapper over _updateCurrentFileIndex that will be called by client
'''
@blueprint.route('/updateCurrentFileIndex', methods=['POST','GET','PUT'])
def updateCurrentFileIndex():

    ''' in PUT request data is received in request.form '''
    experimentId = request.form.get('experimentId', None)
    currentFileIndex = request.form.get('currentFileIndex', None)

    _updateCurrentFileIndex(experimentId, int(currentFileIndex))
    response = {}
    response['success'] = True

    return jsonify(response)


''' delete the annotation of the specified file & experiment '''
@blueprint.route('/deleteAnnotation', methods=['DELETE'])
def deleteAnnotation():

    ''' in DELETE request data is received in request.form '''
    experimentId = request.form.get('experimentId', None)
    fileId = request.form.get('fileId', None)
    lp = request.form.get('lp', None)
    # if int(lp) == 0:
        # fileId = int(fileId) - 1

    AnnotationInfo.query.filter(and_(AnnotationInfo.user_id==current_user.id, \
                                    AnnotationInfo.file_id==fileId)\
                                    ).delete()
    
    db.session.commit()
    response = {}
    response['success'] = True

    return jsonify(response)


'''
    wrapper over _getFile that will be called by client
'''
@blueprint.route('/_getFileDetails', methods=['POST','GET'])
def _getFileDetails():
    experimentId = request.args.get('experimentId', None)
    currentFileIndex = request.args.get('currentFileIndex', None)
    firstFile = request.args.get('firstFile', None)
    currentFile = _getFile(experimentId, int(currentFileIndex), int(firstFile))
    return jsonify(currentFile)

''' (TODO) correct names '''
@blueprint.route('/uploads/<path:filename>')
def download_file(filename):
    from rapidannotator import app
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@blueprint.route('/_addAnnotationInfo', methods=['POST','GET'])
def _addAnnotationInfo():

    for k in request.args:
        arguments = json.loads(k)

    fileId = arguments.get('fileId', None)
    annotations = arguments.get('annotations')
    prevLabelCount = arguments.get('labelCount', None)

    ''' For displaying a warning if the labels got changed at any time'''
    labelCount = 0

    experimentId = File.query.filter_by(id=fileId).first().experiment_id
    
    annotationLevels = AnnotationLevel.query.filter_by(experiment_id=experimentId).all()
    for level in annotationLevels:
        labels = Label.query.filter_by(annotation_id=level.id)
        labelCount += labels.count()

    if labelCount != prevLabelCount:
        response = {}
        response['success'] = False
        return jsonify(response)

    for annotationLevelId in annotations:
        labelId = annotations[annotationLevelId]
        annotationInfo = AnnotationInfo(
            file_id = fileId,
            annotationLevel_id = annotationLevelId,
            label_id = labelId,
            user_id = current_user.id
        )
        db.session.add(annotationInfo)

    annotatorInfo = AnnotatorAssociation.query.filter_by(user_id=current_user.id).\
                    filter_by(experiment_id=experimentId).first()
    annotatorInfo.current = annotatorInfo.current + 1

    db.session.commit()

    response = {}
    response['success'] = True

    return jsonify(response)


@blueprint.route('/_toggleLooping', methods=['POST','GET'])
def _toggleLooping():

    current_user.looping = not current_user.looping
    db.session.commit()

    response = {}
    response['success'] = True

    return jsonify(response)

''' Change the status of the experiment dynamically'''
@blueprint.route('/checkStatus', methods=['POST'])
def checkStatus():
    experimentId = request.form.get('experimentId', None)
    associations = AnnotatorAssociation.query.filter_by(experiment_id=experimentId).all()
    associationsCount = AnnotatorAssociation.query.filter_by(experiment_id=experimentId).count()
    experiment = Experiment.query.filter_by(id=experimentId).first()
    fileCount = experiment.files.count()
    tot = 0
    for association in associations:
        tot += association.current
    if (fileCount*associationsCount) == tot:
        experiment.status = 'Completed'
        experiment.is_done = 1
        db.session.commit()
    response = {}
    response['success'] = True
    return jsonify(response)
